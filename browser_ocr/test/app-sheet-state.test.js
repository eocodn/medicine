"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function classList(initial = []) {
  const values = new Set(initial);
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
    toggle(value, force) {
      if (force === undefined ? !values.has(value) : force) values.add(value);
      else values.delete(value);
    },
  };
}

test("global sheet close cannot cancel or hide a final OCR batch write", () => {
  const backdrop = { classList: classList() };
  const reviewSheet = { classList: classList() };
  const otherSheet = { classList: classList() };
  const nodes = new Map([
    ["#sheet-backdrop", backdrop],
    ["#ocr-review-sheet", reviewSheet],
  ]);
  const document = {
    addEventListener() {},
    querySelector(selector) { return nodes.get(selector) || null; },
    querySelectorAll(selector) { return selector === ".bottom-sheet" ? [reviewSheet, otherSheet] : []; },
  };
  let finalizing = true;
  let cancelled = 0;
  const context = {
    console,
    document,
    localStorage: { getItem() { return null; } },
    MedicineOcrReview: { getState: () => ({ finalizing }) },
    MedicineOcr: {
      getReview: () => ({ operation_id: "ocr-final-write" }),
      cancel() { cancelled += 1; },
    },
    setTimeout,
    clearTimeout,
    Intl,
    Date,
    URLSearchParams,
  };
  context.window = context;
  vm.createContext(context);
  const appPath = path.join(__dirname, "../../medicine_app/static/app.js");
  vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });

  assert.equal(context.closeSheets(), false);
  assert.equal(cancelled, 0);
  assert.equal(backdrop.classList.contains("hidden"), false);
  assert.equal(reviewSheet.classList.contains("hidden"), false);

  finalizing = false;
  assert.equal(context.closeSheets(), true);
  assert.equal(cancelled, 1);
  assert.equal(backdrop.classList.contains("hidden"), true);
  assert.equal(reviewSheet.classList.contains("hidden"), true);
  assert.equal(otherSheet.classList.contains("hidden"), true);
});
