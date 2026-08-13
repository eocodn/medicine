"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const ocrPath = path.resolve(__dirname, "../../medicine_app/static/ocr.js");
const PRIVACY_COPY = "브라우저 안에서 사진을 인식할 수 있어요. 사진은 서버로 전송되지 않아요.";

function harness() {
  const button = { disabled: true, textContent: "" };
  const status = { textContent: "" };
  const toasts = [];
  const nodes = {
    "#ocr-scan-button": button,
    "#ocr-status": status,
  };
  global.window = {
    document: { querySelector(selector) { return nodes[selector] || null; } },
    MedicineBrowserOcr: { postMessage() {} },
    toast(message) { toasts.push(message); },
    addEventListener() {},
  };
  delete require.cache[ocrPath];
  require(ocrPath);
  return { ocr: global.window.MedicineOcr, button, status, toasts };
}

test("keeps stable privacy guidance instead of narrating OCR progress", () => {
  const h = harness();
  for (const state of ["capabilities", "accepted", "scanner_ready", "scanning", "recognizing", "review_required", "cancelled", "finished"]) {
    h.ocr.renderState(state, state === "capabilities" ? { provider: "direct-onnx-wasm-cpu" } : { progress: 73 });
    assert.equal(h.status.textContent, PRIVACY_COPY, state);
  }
  assert.equal(h.status.textContent.includes("73%"), false);
  assert.equal(h.status.textContent.includes("취소했어요"), false);
});

test("keeps privacy guidance visible and surfaces only actionable OCR failures", () => {
  const h = harness();
  h.ocr.renderState("capabilities", { provider: "direct-onnx-wasm-cpu" });
  h.ocr.renderState("failed", { message: "사진을 인식하지 못했어요. 다른 사진으로 다시 시도해주세요." });
  assert.equal(h.status.textContent, PRIVACY_COPY);
  assert.deepEqual(h.toasts, ["사진을 인식하지 못했어요. 다른 사진으로 다시 시도해주세요."]);
});


test("cancel clears a completed OCR review even when the provider emits no terminal event", () => {
  const h = harness();
  assert.equal(h.ocr.start(), true);
  const operationId = h.ocr.getState().operation_id;
  assert.ok(operationId);
  assert.equal(h.ocr.handleEvent({
    schema_version: 1,
    operation_id: operationId,
    sequence: 0,
    state: "review_required",
    hints: { dose_amount: 1, dose_unit: "정" },
    product_queries: ["타이레놀정"],
  }), true);
  assert.ok(h.ocr.getReview());

  assert.equal(h.ocr.cancel(), true);

  assert.equal(h.ocr.getReview(), null);
  assert.equal(h.ocr.getState().operation_id, null);
});
