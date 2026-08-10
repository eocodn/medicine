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
  let terminated = 0;
  const input = {
    files: [], value: "",
    click() { clicked += 1; },
    addEventListener(name, listener) { listeners[name] = listener; },
  };
  const worker = {
    recognize: options.recognize || (async () => ({ data: { text: "약명: 타이레놀정\n1정 1일 2회 7일" } })),
    async terminate() { terminated += 1; },
  };
  global.window = {
    document: { querySelector: () => input },
    Tesseract: { createWorker: async (...args) => { options.onCreate?.(...args); return worker; } },
    MedicineBrowserOcrParser: parser,
    Worker: function Worker() {}, WebAssembly: {}, File: function File() {},
    setTimeout(callback) { timers.push(callback); return timers.length; },
    clearTimeout() {},
    onMedicineNativeEvent(event) { events.push(event); },
  };
  delete require.cache[providerPath];
  require(providerPath);
  return {
    provider: global.window.MedicineBrowserOcr,
    input, listeners, events, timers,
    clicked: () => clicked,
    terminated: () => terminated,
  };
}

function command(provider, name, operationId = null) {
  provider.postMessage(JSON.stringify({
    command: name, schema_version: 1,
    ...(operationId ? { operation_id: operationId } : {}),
  }));
}

test("reports browser capability and emits only structured review data", async () => {
  let workerOptions = null;
  const h = harness({ onCreate: (_languages, _oem, options) => {
    workerOptions = options;
    options.logger({ status: "loading language traineddata", progress: 0.5 });
  } });
  command(h.provider, "get_capabilities");
  assert.equal(h.events.at(-1).capabilities.provider, "browser-wasm");

  command(h.provider, "start_scan", "browser-1");
  assert.equal(h.clicked(), 1);
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  await tick();

  const review = h.events.find((event) => event.state === "review_required");
  assert.deepEqual(review.product_queries, ["타이레놀정"]);
  assert.equal(JSON.stringify(review).includes("1정 1일"), false);
  assert.ok(h.events.some((event) => event.state === "recognizing" && event.progress === 35));
  assert.equal(workerOptions.workerPath, "/ocr-assets/worker.min.js");
  assert.equal(h.terminated(), 1);
  assert.equal(h.input.value, "");
});

test("cancel terminates worker and rejects a stale recognition result", async () => {
  let resolveRecognition;
  const h = harness({ recognize: () => new Promise((resolve) => { resolveRecognition = resolve; }) });
  command(h.provider, "start_scan", "browser-2");
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  command(h.provider, "cancel_scan", "browser-2");
  await tick();
  resolveRecognition({ data: { text: "약명: 뒤늦은약정" } });
  await tick();
  await tick();

  assert.ok(h.events.some((event) => event.state === "cancelled"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
  assert.equal(h.terminated(), 1);
});

test("deadline is an explicit expired state", async () => {
  const h = harness({ recognize: () => new Promise(() => {}) });
  command(h.provider, "start_scan", "browser-3");
  await h.timers[0]();
  await tick();
  assert.ok(h.events.some((event) => event.state === "expired"));
  h.input.value = "late-file";
  h.input.files = [{ name: "late.png" }];
  h.listeners.change();
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
