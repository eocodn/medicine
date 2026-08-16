"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const test = require("node:test");
const assert = require("node:assert/strict");

const root = path.join(__dirname, "..");
const mobile = path.join(root, "mobile");
const manifest = JSON.parse(fs.readFileSync(path.join(mobile, "model-manifest.json"), "utf8"));
const sha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");

test("mobile recognizer is pinned to the selected full-document-only checkpoint", () => {
  const model = path.join(mobile, "korean-recognition.onnx");
  const dictionary = path.join(mobile, "korean-recognition-dictionary.json");
  assert.equal(manifest.selected_recognizer.checkpoint_sha256, "804a57a050096ed34feda8cfa584b1e335685c2703494341cc74ce933c1ee12f");
  assert.equal(sha(model), manifest.selected_recognizer.onnx_sha256);
  assert.equal(fs.statSync(model).size, manifest.selected_recognizer.onnx_size_bytes);
  assert.equal(sha(dictionary), manifest.selected_recognizer.dictionary_sha256);
  assert.deepEqual(manifest.selected_recognizer.fixed_eval_critical_exact, { correct: 8493, total: 8548 });
});

test("mobile runtime keeps FP32 after quantization changed medication predictions", () => {
  assert.equal(manifest.runtime.recognition_precision, "fp32");
  assert.equal(manifest.optimization_decision.selected, "fp32");
  assert.match(manifest.optimization_decision.reason, /changed medication-name predictions/);
});
