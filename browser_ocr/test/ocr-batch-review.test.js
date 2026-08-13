"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const review = require("../../medicine_app/static/ocr-review.js");

function row(overrides = {}) {
  return {
    product_query: "타이레놀정",
    product_ref: "MFDS-A",
    dose_amount: 1,
    dose_unit: "정",
    frequency_per_day: 2,
    prescription_days: 7,
    schedule_times: ["08:00", "20:00"],
    meal_relation: "after_meal",
    administration_route: "oral",
    as_needed: false,
    association: "table_row",
    ...overrides,
  };
}

test("normalizes OCR rows into fully editable medication records", () => {
  const rows = review.normalizeRows([
    row(),
    row({ product_query: "이부프로펜정", product_ref: "MFDS-B", dose_amount: 2, frequency_per_day: 3, prescription_days: 5 }),
  ], "2026-08-13");

  assert.equal(rows.length, 2);
  assert.equal(rows[0].row_id, "ocr-1");
  assert.equal(rows[0].product_query, "타이레놀정");
  assert.equal(rows[1].dose_amount, "2");
  assert.equal(rows[1].frequency_per_day, "3");
  assert.equal(rows[1].prescription_days, "5");
  assert.equal(rows[0].schedule_times, "08:00, 20:00");
  assert.equal(rows[0].start_date, "2026-08-13");
});

test("reviews the whole edited set and then creates it atomically after one batch warning confirmation", async () => {
  const calls = [];
  let created = null;
  review.init({
    currentPersonId: () => "person-1",
    today: () => "2026-08-13",
    randomId: () => "request-1",
    api: async (path, options) => {
      const body = JSON.parse(options.body);
      calls.push([path, body]);
      if (path.endsWith("/batch-preview")) {
        return {
          requires_review: true,
          warning_token: "warn-1",
          ocr_review_token: "ocr-review-1",
          rows: body.rows.map((item) => ({ row_id: item.row_id, assessment: { dur_checks: [{ category: "combination_contraindication", status: "hit" }] } })),
        };
      }
      return { medications: body.rows.map((item) => ({ id: `med-${item.row_id}` })) };
    },
    onCreated(result) { created = result; },
  });
  review.open([row(), row({ product_query: "이부프로펜정", product_ref: "MFDS-B" })], "ocr-operation-1", null);

  const first = await review.submit();
  assert.equal(first.reviewRequired, true);
  assert.equal(calls.length, 1);
  assert.ok(calls[0][0].endsWith("/batch-preview"));
  assert.equal(calls[0][1].rows.length, 2);

  const second = await review.submit();
  assert.equal(second.created, true);
  assert.equal(calls.length, 2);
  assert.ok(calls[1][0].endsWith("/batch"));
  assert.equal(calls[1][1].acknowledge_warnings, true);
  assert.equal(calls[1][1].warning_token, "warn-1");
  assert.equal(calls[1][1].ocr_review_token, "ocr-review-1");
  assert.equal(calls[1][1].request_id, "request-1");
  assert.equal(calls[1][1].rows.length, 2);
  assert.ok(created);
});

test("editing any field after safety review invalidates the reviewed batch and forces a new preview", async () => {
  const paths = [];
  review.init({
    currentPersonId: () => "person-1",
    today: () => "2026-08-13",
    randomId: () => "request-2",
    api: async (path, options) => {
      paths.push(path);
      const body = JSON.parse(options.body);
      return {
        requires_review: true,
        warning_token: `warn-${paths.length}`,
        ocr_review_token: `ocr-${paths.length}`,
        rows: body.rows.map((item) => ({ row_id: item.row_id, assessment: { dur_checks: [] } })),
      };
    },
  });
  review.open([row()], "ocr-operation-2", null);
  await review.submit();
  review.updateField("ocr-1", "dose_amount", "2");
  await review.submit();

  assert.equal(paths.length, 2);
  assert.ok(paths.every((path) => path.endsWith("/batch-preview")));
  assert.equal(review.getState().rows[0].dose_amount, "2");
});

test("renders every OCR medication row as an editable correction form", () => {
  const root = {
    innerHTML: "",
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  review.init({ root, today: () => "2026-08-13", randomId: () => "request-ui" });
  review.open([
    row(),
    row({ product_query: "이부프로펜정", product_ref: "MFDS-B" }),
  ], "ocr-operation-ui", null);

  assert.match(root.innerHTML, /약 1/);
  assert.match(root.innerHTML, /약 2/);
  for (const field of [
    "product_query", "dose_amount", "dose_unit", "frequency_per_day", "prescription_days",
    "schedule_times", "meal_relation", "administration_route", "start_date", "as_needed",
  ]) {
    assert.match(root.innerHTML, new RegExp(`data-ocr-field="${field}"`), field);
  }
  assert.match(root.innerHTML, /전체 확인하고 일괄 등록/);
});

test("expired batch review returns to authoritative preview instead of retrying a stale token", async () => {
  const paths = [];
  let createAttempts = 0;
  review.init({
    currentPersonId: () => "person-1",
    today: () => "2026-08-13",
    randomId: () => "request-expired",
    api: async (path, options) => {
      paths.push(path);
      const body = JSON.parse(options.body);
      if (path.endsWith("/batch-preview")) return {
        requires_review: true,
        warning_token: `warn-${paths.length}`,
        ocr_review_token: `review-${paths.length}`,
        rows: body.rows.map((item) => ({ row_id: item.row_id, assessment: { dur_checks: [] } })),
      };
      createAttempts += 1;
      const error = new Error("ocr_review_token does not match the reviewed medication batch");
      error.status = 400;
      throw error;
    },
  });
  review.open([row()], "ocr-expired", null);
  await review.submit();
  const expired = await review.submit();
  assert.equal(expired.reviewExpired, true);
  assert.equal(review.getState().phase, "editing");

  await review.submit();
  assert.equal(paths.filter((path) => path.endsWith("/batch-preview")).length, 2);
  assert.equal(createAttempts, 1);
});

test("identifies the row when edited schedule fields are internally inconsistent", async () => {
  const toasts = [];
  let apiCalls = 0;
  review.init({
    currentPersonId: () => "person-1",
    today: () => "2026-08-13",
    randomId: () => "request-invalid",
    toast(message) { toasts.push(message); },
    api: async () => { apiCalls += 1; throw new Error("should not call api"); },
  });
  review.open([row({ frequency_per_day: 2, schedule_times: ["08:00"] })], "ocr-invalid", null);

  const result = await review.submit();
  assert.equal(result.invalid, true);
  assert.equal(apiCalls, 0);
  assert.match(toasts.at(-1), /약 1/);
  assert.match(toasts.at(-1), /복용 시간/);
});


test("discards a delayed product-search response after the user edits that row", async () => {
  let resolveSearch;
  const delayed = new Promise((resolve) => { resolveSearch = resolve; });
  review.init({
    currentPersonId: () => "person-1",
    today: () => "2026-08-13",
    randomId: () => "request-race",
    api: async (path) => {
      if (path.startsWith("/api/products?")) return delayed;
      throw new Error("unexpected api call");
    },
  });
  review.open([row({ product_query: "원래약", product_ref: null })], "ocr-race", null);
  review.updateField("ocr-1", "product_query", "사용자수정약");
  resolveSearch([{ product_ref: "P1", product_name: "원래약", suggested_administration_route: "oral" }]);
  await new Promise((resolve) => setImmediate(resolve));

  const current = review.getState().rows[0];
  assert.equal(current.product_query, "사용자수정약");
  assert.equal(current.product_ref, null);
  assert.equal(current.administration_route, "oral");
});
