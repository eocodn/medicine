"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeOcrRows } = require("../../medicine_app/static/ocr-review.js");

test("OCR review keeps only editable structured draft fields", () => {
  const rows = normalizeOcrRows([{
    row_id: "ocr-row-1",
    product_query: " 타진서방정 10/5mg ",
    draft: {
      dose_amount: 1,
      dose_unit: "tablet",
      frequency_per_day: 2,
      prescription_days: 7,
      raw_text: "must never cross the product boundary",
    },
    uncertainty_codes: ["LOW_CONFIDENCE_OCR", "LOW_CONFIDENCE_OCR"],
    image_path: "/tmp/nope.jpg",
  }]);

  assert.deepEqual(rows, [{
    row_id: "ocr-row-1",
    product_query: "타진서방정 10/5mg",
    draft: { dose_amount: 1, dose_unit: "tablet", frequency_per_day: 2, prescription_days: 7 },
    uncertainty_codes: ["LOW_CONFIDENCE_OCR"],
  }]);
});

test("product shell exposes local-only image review without an OCR backend route", () => {
  const index = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/index.html"), "utf8");
  const review = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/ocr-review.js"), "utf8");

  assert.match(index, /id="ocr-image-input"/);
  assert.match(index, /accept="image\/\*"/);
  assert.match(index, /사진은 서버로 전송되지 않/);
  assert.match(index, /ocr-review\.js/);
  assert.match(review, /new Worker\("\/ocr-assets\/direct\/ocr-worker\.js"\)/);
  assert.doesNotMatch(review, /appassets\.androidplatform\.net/);
  assert.match(review, /인식 정확도가 낮아/);
  assert.doesNotMatch(review, /OCR 확인 항목:/);
  assert.doesNotMatch(review, /fetch\(|\/api\/ocr|MedicineNative/);
});

test("OCR row selection only seeds canonical search and editable prescription draft", () => {
  const app = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/app.js"), "utf8");
  const prescription = fs.readFileSync(path.join(__dirname, "../../medicine_app/static/prescription.js"), "utf8");

  assert.match(app, /pendingOcrDraft/);
  assert.match(app, /medicine:ocr-select/);
  assert.match(app, /ocr-review-panel/);
  assert.match(app, /scrollIntoView/);
  assert.match(prescription, /applyOcrDraftToForm/);
  assert.match(prescription, /previewProduct\(productRef, ocrDraft/);
  assert.doesNotMatch(app, /medicine:ocr-select[\s\S]{0,800}confirmAddMedication\(/);
});
