"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ROOT = path.resolve(__dirname, "../..");
const source = (relative) => fs.readFileSync(path.join(ROOT, relative), "utf8");

function poly(x1, y1, x2, y2) {
  return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]];
}

test("OCR medication queries prefer longer contiguous spans then single regions", () => {
  const { buildMedicationQueries } = require("../../ui/dist/ocr-intake.js");
  const queries = buildMedicationQueries([
    { id: "a", text: "덱스트로", poly: poly(10, 10, 80, 30) },
    { id: "b", text: "메토르판캡슐", poly: poly(85, 10, 190, 30) },
    { id: "c", text: "15mg", poly: poly(195, 10, 235, 30) },
    { id: "d", text: "본인부담금", poly: poly(10, 90, 100, 110) },
  ]);
  assert.deepEqual(queries.slice(0, 3).map((query) => [query.text, query.node_ids]), [
    ["덱스트로 메토르판캡슐 15mg", ["a", "b", "c"]],
    ["덱스트로 메토르판캡슐", ["a", "b"]],
    ["덱스트로", ["a"]],
  ]);
  assert.ok(queries.some((query) => query.text === "본인부담금"));
});

test("OCR medication queries do not stitch distant regions", () => {
  const { buildMedicationQueries } = require("../../ui/dist/ocr-intake.js");
  const queries = buildMedicationQueries([
    { id: "a", text: "아세트아미노펜정", poly: poly(10, 10, 160, 30) },
    { id: "b", text: "500mg", poly: poly(500, 10, 550, 30) },
  ]);
  assert.deepEqual(queries.map((query) => query.text), ["아세트아미노펜정", "500mg"]);
});

test("direct OCR worker returns OCR items without a learned parser dependency", () => {
  const worker = source("ocr_runtime/src/direct-ocr-worker.js");
  assert.doesNotMatch(worker, /runParserModel|PARSER_ENABLED|parser\.onnx|parser-manifest/);
  assert.match(worker, /items/);
});

test("OCR intake sends observations to catalog-backed medication discovery", () => {
  const intake = source("ui/dist/ocr-intake.js");
  const app = source("ui/dist/app.js");
  const state = source("ui/dist/app-state.js");
  const prescription = source("ui/dist/prescription.js");
  assert.match(intake, /medicine:ocr-result/);
  assert.match(app, /medicine:ocr-result/);
  assert.match(intake, /\/api\/products\/ocr-candidates/);
  assert.doesNotMatch(intake, /draft|uncertainty|parser_status|medicine:parser-result/);
  assert.doesNotMatch(app, /Parser|parser|pendingParser|uncertainty/i);
  assert.doesNotMatch(state, /Parser|parser|uncertainty/i);
  assert.doesNotMatch(prescription, /Parser|parser|uncertainty/i);
});

test("OCR discovery rows carry only search identity", async () => {
  const { discoverMedicationRows } = require("../../ui/dist/ocr-intake.js");
  const rows = await discoverMedicationRows([
    { id: "a", text: "타이레놀정", poly: poly(10, 10, 100, 30) },
  ], async () => ({
    rows: [{ row_id: "ocr-q-001", product_query: "타이레놀정", draft: { dose_amount: 1 }, uncertainty_codes: ["X"] }],
  }));
  assert.deepEqual(rows, [{ row_id: "ocr-q-001", product_query: "타이레놀정" }]);
});
