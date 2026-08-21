"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "../..");
const source = (relative) => fs.readFileSync(path.join(ROOT, relative), "utf8");

test("parser rows keep structured draft and uncertainty without a review form", () => {
  const { normalizeParserRows } = require("../../medicine_app/static/ocr-intake.js");
  assert.deepEqual(normalizeParserRows([{
    row_id: "row-1",
    product_query: " 타이레놀정 ",
    draft: {
      dose_amount: 1,
      dose_unit: "tablet",
      frequency_per_day: 3,
      prescription_days: 5,
      schedule_times: ["08:00", "13:00", "19:00"],
    },
    uncertainty_codes: ["LOW_CONFIDENCE_DOSE", "LOW_CONFIDENCE_DOSE"],
  }]), [{
    row_id: "row-1",
    product_query: "타이레놀정",
    draft: {
      dose_amount: 1,
      dose_unit: "tablet",
      frequency_per_day: 3,
      prescription_days: 5,
      schedule_times: ["08:00", "13:00", "19:00"],
    },
    uncertainty_codes: ["LOW_CONFIDENCE_DOSE"],
  }]);
});

test("invalid parser fields are dropped and reported without aborting the row", () => {
  const { normalizeParserRows } = require("../../medicine_app/static/ocr-intake.js");
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args);
  try {
    assert.deepEqual(normalizeParserRows([{
      row_id: "row-1",
      product_query: "타이레놀정",
      draft: {
        dose_amount: 1,
        frequency_per_day: 1.5,
        schedule_times: ["08:00", "not-a-time", "08:00"],
        meal_relation: "sometimes",
        administration_route: "oral",
      },
      uncertainty_codes: ["LOW_CONFIDENCE_DOSE", "not-valid"],
    }]), [{
      row_id: "row-1",
      product_query: "타이레놀정",
      draft: {
        dose_amount: 1,
        schedule_times: ["08:00"],
        administration_route: "oral",
      },
      uncertainty_codes: ["LOW_CONFIDENCE_DOSE"],
    }]);
  } finally {
    console.warn = originalWarn;
  }

  assert.equal(warnings.length, 1);
  assert.equal(warnings[0][0], "medicine parser output sanitized");
  const diagnostic = warnings[0][1];
  assert.equal(diagnostic.event, "parser_output_sanitized");
  assert.ok(diagnostic.issues.some((issue) => issue.field === "frequency_per_day"));
  assert.ok(diagnostic.issues.some((issue) => issue.field === "schedule_times"));
  assert.ok(diagnostic.issues.some((issue) => issue.field === "meal_relation"));
  assert.ok(diagnostic.issues.some((issue) => issue.field === "uncertainty_codes"));
  assert.ok(diagnostic.issues.every((issue) => !Object.hasOwn(issue, "value")));
});

test("parser result bypasses review and enters the generic product-search flow", () => {
  const intake = source("medicine_app/static/ocr-intake.js");
  const app = source("medicine_app/static/app.js");
  const index = source("medicine_app/static/index.html");
  const state = source("medicine_app/static/app-state.js");
  const prescription = source("medicine_app/static/prescription.js");

  assert.match(intake, /medicine:parser-result/);
  assert.match(app, /medicine:parser-result/);
  assert.match(app, /product_query[\s\S]{0,900}runDrugSearch/);
  assert.match(index, /ocr-intake\.js/);
  assert.match(index, /placeholder="제품명"/);
  assert.match(state, /pendingParserDraft/);
  assert.match(state, /pendingParserUncertaintyCodes/);
  assert.match(prescription, /pendingParserUncertaintyCodes/);
});

test("direct OCR worker no longer embeds a rule parser", () => {
  const worker = source("browser_ocr/src/direct-ocr-worker.js");
  assert.match(worker, /parser_status:\s*"unavailable"/);
});
