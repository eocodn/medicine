"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const index = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/index.html"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/app.js"), "utf8");
const prescription = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/prescription.js"), "utf8");
const dialog = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/dialog.js"), "utf8");


test("profile form only offers male/female and binary female reproductive states", () => {
  const sexBlock = index.match(/<select name="sex"[^>]*>([\s\S]*?)<\/select>/)?.[1] || "";
  const pregnancyBlock = index.match(/<select name="pregnancy_status"[^>]*>([\s\S]*?)<\/select>/)?.[1] || "";
  const lactationBlock = index.match(/<select name="lactation_status"[^>]*>([\s\S]*?)<\/select>/)?.[1] || "";

  assert.match(sexBlock, /value="female"/);
  assert.match(sexBlock, /value="male"/);
  assert.doesNotMatch(sexBlock, /value="unknown"|value="other"/);
  assert.match(pregnancyBlock, /value="pregnant"/);
  assert.match(pregnancyBlock, /value="not_pregnant"/);
  assert.doesNotMatch(pregnancyBlock, /value="unknown"|value="not_applicable"/);
  assert.match(lactationBlock, /value="breastfeeding"/);
  assert.match(lactationBlock, /value="not_breastfeeding"/);
  assert.doesNotMatch(lactationBlock, /value="unknown"|value="not_applicable"/);
});


test("shared UI exposes long-term and PRN maximum-use fields and treats PRN as a separate regimen mode", () => {
  assert.match(prescription, /pending-long-term/);
  assert.match(prescription, /pending-prn-max/);
  assert.match(prescription, /long_term:/);
  assert.match(prescription, /prn_max_per_day:/);
  assert.match(prescription, /syncPrnFields/);
});


test("current medication cards distinguish unresolved review state from DUR hit", () => {
  assert.match(app, /dur_review_required/);
  assert.match(app, /dur-review-badge/);
});


test("skipped dose UI offers an undo action", () => {
  assert.match(app, /item\.status === "skipped"[\s\S]*data-instance-cancel/);
});


test("app refreshes dashboard when Korea calendar date changes", () => {
  assert.match(app, /refreshForDateChange/);
  assert.match(app, /visibilitychange/);
  assert.match(app, /setInterval\(refreshForDateChange/);
});


test("bottom sheets implement modal focus and escape handling", () => {
  assert.match(dialog, /trapSheetFocus/);
  assert.match(dialog, /event\.key === "Escape"/);
  assert.match(dialog, /\.inert\s*=/);
});


test("search result copy has a partial DUR coverage state", () => {
  assert.match(app, /dur_coverage_status/);
  assert.match(app, /DUR 일부 기준 확인 필요/);
});


test("PRN medication card exposes actual-intake recording", () => {
  assert.match(app, /data-prn-taken/);
  assert.match(prescription, /prn-intakes/);
});


test("recent instance-linked history can be corrected from the shared UI", () => {
  assert.match(app, /data-log-cancel/);
  assert.match(app, /cancelDoseInstance\(button\.dataset\.logCancel\)/);
});
