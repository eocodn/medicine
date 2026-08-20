"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticRoot = path.join(__dirname, "../../medicine_app/static");

function source(name) {
  return fs.readFileSync(path.join(staticRoot, name), "utf8");
}

function exposeOcrInternals(names, root) {
  const original = source("ocr-review.js");
  const exposed = original.replace(
    /return \{ normalizeOcrRows(?:, reset)? \};/,
    `return { normalizeOcrRows, reset, ${names.filter((name) => name !== "reset").join(", ")} };`,
  );
  const context = {
    window: root,
    globalThis: root,
    module: { exports: {} },
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(exposed, context);
  return context.module.exports;
}

function classList(initial = []) {
  const values = new Set(initial);
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

function productSearchContext() {
  const query = { value: "씬지록신 25" };
  const status = { textContent: "" };
  const results = { innerHTML: "", querySelectorAll() { return []; } };
  const hero = { classList: { toggle() {} } };
  const document = {
    querySelector(selector) {
      return {
        "#drug-query": query,
        "#search-status": status,
        "#drug-results": results,
        ".search-hero": hero,
      }[selector] || null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: { MedicineLocalApi: null, MedicineOcrReview: { reset() {} } },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    friendlyErrorMessage: (value) => String(value),
    console,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    fetch: async () => { throw new Error("unexpected fetch"); },
  };
  vm.createContext(context);
  vm.runInContext(source("app-state.js"), context);
  vm.runInContext(source("app.js"), context);
  return { context, query, status, results };
}

test("zero-row OCR stays a failure state instead of opening an empty review", () => {
  const panel = { classList: classList(["hidden"]) };
  const list = { innerHTML: "stale", querySelectorAll() { return []; } };
  const root = {
    document: {
      addEventListener() {},
      querySelector(selector) {
        if (selector === "#ocr-review-panel") return panel;
        if (selector === "#ocr-review-list") return list;
        return null;
      },
    },
  };
  const { renderRows } = exposeOcrInternals(["renderRows"], root);

  renderRows([]);

  assert.equal(panel.classList.contains("hidden"), true);
  assert.equal(list.innerHTML, "");
});

test("OCR review preserves every supported regimen field through row confirmation", () => {
  const root = {};
  const { readRow } = exposeOcrInternals(["readRow"], root);
  const values = {
    product_query: "타진서방정",
    dose_amount: "1",
    dose_unit: "정",
    frequency_per_day: "2",
    prescription_days: "7",
    schedule_times: "08:00, 20:00",
    meal_relation: "after_meal",
    administration_route: "oral",
    as_needed: "",
  };
  const card = {
    querySelector(selector) {
      const match = selector.match(/data-ocr-field="([^"]+)"/);
      if (!match) return null;
      const field = match[1];
      return field === "as_needed"
        ? { checked: true, value: "" }
        : { value: values[field] || "" };
    },
  };
  const base = {
    row_id: "row-1",
    product_query: "타진서방정",
    draft: {
      schedule_times: ["07:00"],
      meal_relation: "before_meal",
      administration_route: "oral",
      as_needed: true,
    },
    uncertainty_codes: [],
  };

  assert.deepEqual(JSON.parse(JSON.stringify(readRow(card, base))), {
    ...base,
    product_query: "타진서방정",
    draft: {
      dose_amount: 1,
      dose_unit: "정",
      frequency_per_day: 2,
      prescription_days: 7,
      schedule_times: ["08:00", "20:00"],
      meal_relation: "after_meal",
      administration_route: "oral",
      as_needed: true,
    },
  });

  const review = source("ocr-review.js");
  for (const field of ["schedule_times", "meal_relation", "administration_route", "as_needed"]) {
    assert.match(review, new RegExp(`data-ocr-field=\\"${field}\\"`));
  }
});

test("leaving Search owns and clears the entire OCR transient session", () => {
  const app = source("app.js");
  const review = source("ocr-review.js");

  assert.match(app, /function resetOcrTransientState/);
  assert.match(app, /previousScreen === "search"[\s\S]{0,240}resetOcrTransientState/);
  assert.match(app, /MedicineOcrReview\?\.reset\?\.\(\)/);
  assert.match(review, /function reset\(/);
  assert.match(review, /return \{ normalizeOcrRows, reset \};/);
});

test("OCR-derived prescription drafts are bound to the active profile", () => {
  const app = source("app.js");
  const people = source("people.js");

  assert.match(app, /pendingOcrPersonId/);
  assert.match(app, /pendingOcrPersonId !== state\.currentPersonId/);
  assert.match(people, /selectPerson[\s\S]{0,320}resetOcrTransientState/);
});

test("visible OCR import control mirrors disabled and busy state", () => {
  const review = source("ocr-review.js");
  const styles = source("styles.css");

  assert.match(review, /\.ocr-file-button/);
  assert.match(review, /aria-disabled/);
  assert.match(review, /is-disabled/);
  assert.match(styles, /\.ocr-file-button\.is-disabled/);
});

test("profile form close discards edit-session state and every add path reinitializes", () => {
  const app = source("app.js");
  const people = source("people.js");
  const dialog = source("dialog.js");

  assert.match(app, /home-add-person[\s\S]{0,180}openPersonForm\(\)/);
  assert.match(dialog, /medicine:sheet-closed/);
  assert.match(people, /medicine:sheet-closed[\s\S]{0,320}editingPersonId = null/);
  assert.match(people, /medicine:sheet-closed[\s\S]{0,320}form\.reset\(\)/);
});

test("editing a drug query immediately removes stale clickable search results", () => {
  const app = source("app.js");

  assert.match(app, /#drug-query[\s\S]{0,520}#search-status[\s\S]{0,180}textContent = ""[\s\S]{0,220}#drug-results[\s\S]{0,180}innerHTML = ""[\s\S]{0,260}setTimeout\(runDrugSearch, 280\)/);
});

test("editing a drug query invalidates any in-flight OCR search before debounce", () => {
  const app = source("app.js");

  assert.match(app, /function invalidateProductSearch\(\)[\s\S]{0,220}state\.searchRequestId \+= 1[\s\S]{0,220}clearTimeout\(state\.searchTimer\)/);
  assert.match(app, /#drug-query[\s\S]{0,520}addEventListener\("input"[\s\S]{0,220}invalidateProductSearch\(\)[\s\S]{0,220}resetOcrTransientState\(\)[\s\S]{0,320}setTimeout\(runDrugSearch, 280\)/);
});

test("trim-equivalent manual edit rejects an already in-flight OCR search response", async () => {
  const { context, query, results } = productSearchContext();
  let resolveRequest;
  let requestPath = "";
  context.window.MedicineLocalApi = {
    request(path) {
      requestPath = path;
      return new Promise((resolve) => { resolveRequest = resolve; });
    },
  };

  vm.runInContext("state.ocrSearchActive = true", context);
  const pending = vm.runInContext("runDrugSearch()", context);
  assert.match(requestPath, /mode=ocr/);

  query.value = "씬지록신 25 ";
  vm.runInContext("invalidateProductSearch(); resetOcrTransientState();", context);
  resolveRequest([{ product_ref: "stale", product_name: "stale" }]);

  assert.equal(await pending, false);
  assert.equal(results.innerHTML, "");
  assert.equal(vm.runInContext("state.ocrSearchActive", context), false);
});
