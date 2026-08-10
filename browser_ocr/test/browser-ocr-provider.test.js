"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const parser = require("../../medicine_app/static/browser-ocr-parser.js");

const providerPath = path.resolve(__dirname, "../../medicine_app/static/browser-ocr.js");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function harness(options = {}) {
  const listeners = {};
  const events = [];
  const timers = [];
  let clicked = 0;
  let disposed = 0;
  const input = {
    files: [], value: "",
    click() { clicked += 1; },
    addEventListener(name, listener) { listeners[name] = listener; },
  };
  const engine = {
    predict: options.predict || (async () => [{ items: [
      { text: "약명: 타이레놀정", score: 0.99 },
      { text: "1정 1일 2회 7일", score: 0.98 },
    ] }]),
    async dispose() { disposed += 1; },
  };
  global.window = {
    document: { querySelector: () => input },
    MedicineBrowserOcrParser: parser,
    MedicinePaddleOcrLoader: async () => ({ PaddleOCR: {
      create: async (createOptions) => { options.onCreate?.(createOptions); return engine; },
    } }),
    Worker: function Worker() {}, WebAssembly: {}, File: function File() {},
    setTimeout(callback) { timers.push(callback); return timers.length; },
    clearTimeout() {},
    setInterval() { return 1; },
    clearInterval() {},
    onMedicineNativeEvent(event) { events.push(event); },
  };
  delete require.cache[providerPath];
  require(providerPath);
  return {
    provider: global.window.MedicineBrowserOcr,
    input, listeners, events, timers,
    clicked: () => clicked,
    disposed: () => disposed,
  };
}

function command(provider, name, operationId = null) {
  provider.postMessage(JSON.stringify({
    command: name, schema_version: 1,
    ...(operationId ? { operation_id: operationId } : {}),
  }));
}

test("uses only the local CPU/WASM PaddleOCR models and emits structured review data", async () => {
  let createOptions = null;
  const h = harness({ onCreate: (options) => { createOptions = options; } });
  command(h.provider, "get_capabilities");
  assert.equal(h.events.at(-1).capabilities.provider, "paddleocr-wasm-cpu");
  assert.equal(h.events.at(-1).capabilities.backend, "wasm");

  command(h.provider, "start_scan", "browser-1");
  assert.equal(h.clicked(), 1);
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  await tick();

  const review = h.events.find((event) => event.state === "review_required");
  assert.deepEqual(review.product_queries, ["타이레놀정"]);
  assert.equal(JSON.stringify(review).includes("1정 1일"), false);
  assert.equal(createOptions.worker, true);
  assert.deepEqual(createOptions.ortOptions, {
    backend: "wasm", wasmPaths: "/ocr-assets/ort/", numThreads: 1, simd: true,
  });
  assert.equal(createOptions.textDetectionModelName, "PP-OCRv5_mobile_det");
  assert.equal(createOptions.textDetectionModelAsset.url, "/ocr-assets/models/PP-OCRv5_mobile_det_onnx_infer.tar");
  assert.equal(createOptions.textRecognitionModelName, "korean_PP-OCRv5_mobile_rec");
  assert.equal(createOptions.textRecognitionModelAsset.url, "/ocr-assets/models/korean_PP-OCRv5_mobile_rec_onnx_infer.tar");
  assert.equal(h.disposed(), 1);
  assert.equal(h.input.value, "");
});

test("cancel terminates worker and rejects a stale recognition result", async () => {
  let resolveRecognition;
  const h = harness({ predict: () => new Promise((resolve) => { resolveRecognition = resolve; }) });
  command(h.provider, "start_scan", "browser-2");
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  command(h.provider, "cancel_scan", "browser-2");
  await tick();
  resolveRecognition([{ items: [{ text: "약명: 뒤늦은약정", score: 0.9 }] }]);
  await tick();
  await tick();

  assert.ok(h.events.some((event) => event.state === "cancelled"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
  assert.equal(h.disposed(), 1);
});

test("deadline expires in-flight prediction and rejects its stale result", async () => {
  let resolvePrediction;
  const h = harness({ predict: () => new Promise((resolve) => { resolvePrediction = resolve; }) });
  command(h.provider, "start_scan", "browser-3");
  h.input.files = [{ name: "slow.png" }];
  h.listeners.change();
  await tick();
  await h.timers[0]();
  await tick();
  assert.ok(h.events.some((event) => event.state === "expired"));
  resolvePrediction([{ items: [{ text: "약명: 뒤늦은약정", score: 0.9 }] }]);
  await tick();
  await tick();
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
  assert.equal(h.disposed(), 1);
  assert.equal(h.input.value, "");
});

test("a concurrent start fails only the rejected operation", async () => {
  const h = harness();
  command(h.provider, "start_scan", "browser-active");
  command(h.provider, "start_scan", "browser-rejected");
  await h.timers[0]();
  await tick();

  const terminal = h.events.filter((event) => ["failed", "expired", "cancelled"].includes(event.state));
  assert.deepEqual(
    terminal.map((event) => [event.operation_id, event.state]),
    [["browser-rejected", "failed"], ["browser-active", "expired"]],
  );
});

test("model initialization failure is explicit and does not fall back", async () => {
  const h = harness();
  global.window.MedicinePaddleOcrLoader = async () => { throw new Error("model unavailable"); };
  command(h.provider, "start_scan", "browser-failed");
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  await tick();

  assert.ok(h.events.some((event) => event.state === "failed"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
});
