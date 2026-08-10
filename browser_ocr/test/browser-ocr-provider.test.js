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
  let workersCreated = 0;
  let workersTerminated = 0;
  const bitmap = {
    width: options.imageWidth || 2400,
    height: options.imageHeight || 1600,
    close() {},
  };
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => ({ fillStyle: "", fillRect() {}, drawImage() {} }),
    toBlob(callback, type, quality) {
      options.onResize?.({ width: canvas.width, height: canvas.height, type, quality });
      callback({ type, width: canvas.width, height: canvas.height });
    },
  };
  const input = {
    files: [], value: "",
    click() { clicked += 1; },
    addEventListener(name, listener) { listeners[name] = listener; },
  };
  const defaultPrediction = async () => [{ items: [
    { text: "약명: 타이레놀정", score: 0.99 },
    { text: "1정 1일 2회 7일", score: 0.98 },
  ] }];
  const engine = {
    async predict(...args) {
      options.onPredict?.(...args);
      return (options.predict || defaultPrediction)(...args);
    },
    async dispose() { disposed += 1; },
  };
  global.window = {
    document: {
      querySelector: () => input,
      createElement: (name) => name === "canvas" ? canvas : null,
    },
    MedicineBrowserOcrParser: parser,
    MedicinePaddleOcrLoader: async () => ({ PaddleOCR: {
      create: async (createOptions) => {
        options.onCreate?.(createOptions);
        createOptions.worker?.createWorker?.();
        return engine;
      },
    } }),
    Worker: function Worker(url, workerOptions) {
      workersCreated += 1;
      options.onWorkerCreate?.({ url, workerOptions });
      let terminated = false;
      this.terminate = () => {
        if (terminated) return;
        terminated = true;
        workersTerminated += 1;
      };
    },
    WebAssembly: {}, File: function File() {},
    createImageBitmap: async () => bitmap,
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
    workersCreated: () => workersCreated,
    workersTerminated: () => workersTerminated,
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
  let resized = null;
  let createdWorker = null;
  let predictionParams = null;
  const h = harness({
    onCreate: (options) => { createOptions = options; },
    onResize: (dimensions) => { resized = dimensions; },
    onWorkerCreate: (worker) => { createdWorker = worker; },
    onPredict: (_, params) => { predictionParams = params; },
  });
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
  assert.equal(typeof createOptions.worker.createWorker, "function");
  assert.deepEqual(createdWorker, {
    url: "/ocr-assets/paddle/assets/worker-entry-C9UNuyOJ.js",
    workerOptions: { type: "module" },
  });
  assert.deepEqual(resized, {
    width: 1280, height: 853, type: "image/jpeg", quality: 0.9,
  });
  assert.deepEqual(predictionParams, { text_det_limit_side_len: 640 });
  assert.deepEqual(createOptions.ortOptions, {
    backend: "wasm", wasmPaths: "/ocr-assets/ort/", numThreads: 1, simd: true,
  });
  assert.equal(createOptions.textDetectionModelName, "PP-OCRv5_mobile_det");
  assert.equal(createOptions.textDetectionModelAsset.url, "/ocr-assets/models/PP-OCRv5_mobile_det_onnx_infer.tar");
  assert.equal(createOptions.textRecognitionModelName, "korean_PP-OCRv5_mobile_rec");
  assert.equal(createOptions.textRecognitionModelAsset.url, "/ocr-assets/models/korean_PP-OCRv5_mobile_rec_onnx_infer.tar");
  assert.equal(h.disposed(), 1);
  assert.equal(h.workersTerminated(), 1);
  assert.equal(h.input.value, "");
});

test("keeps an image at or below the pixel limit without re-encoding it", async () => {
  let predicted = null;
  const h = harness({
    imageWidth: 960,
    imageHeight: 640,
    predict: async (source) => {
      predicted = source;
      return [{ items: [{ text: "약명: 타이레놀정" }] }];
    },
  });
  const selected = { name: "small.png" };
  command(h.provider, "start_scan", "browser-small");
  h.input.files = [selected];
  h.listeners.change();
  await tick();
  await tick();

  assert.equal(predicted, selected);
});

test("cancel terminates worker and rejects a stale recognition result", async () => {
  let resolveRecognition;
  const h = harness({ predict: () => new Promise((resolve) => { resolveRecognition = resolve; }) });
  command(h.provider, "start_scan", "browser-2");
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  assert.equal(h.workersCreated(), 1);
  command(h.provider, "cancel_scan", "browser-2");
  await tick();
  assert.equal(h.workersTerminated(), 1);
  resolveRecognition([{ items: [{ text: "약명: 뒤늦은약정", score: 0.9 }] }]);
  await tick();
  await tick();

  assert.ok(h.events.some((event) => event.state === "cancelled"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
  assert.equal(h.disposed(), 1);
  assert.equal(h.workersTerminated(), 1);
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
