"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const parser = require("../../medicine_app/static/browser-ocr-parser.js");

const providerPath = path.resolve(__dirname, "../../medicine_app/static/browser-ocr.js");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function harness() {
  const listeners = {};
  const events = [];
  const timers = [];
  const workers = [];
  let clicked = 0;
  const input = {
    files: [], value: "",
    click() { clicked += 1; },
    addEventListener(name, listener) { listeners[name] = listener; },
  };

  function Worker(url, options) {
    this.url = url;
    this.options = options;
    this.messages = [];
    this.terminated = false;
    this.postMessage = (message) => { this.messages.push(message); };
    this.terminate = () => { this.terminated = true; };
    this.emit = (data) => this.onmessage?.({ data });
    this.fail = (message) => this.onerror?.({ message });
    workers.push(this);
  }

  global.window = {
    document: { querySelector: () => input },
    MedicineBrowserOcrParser: parser,
    Worker, WebAssembly: {}, File: function File() {},
    setTimeout(callback) { timers.push(callback); return timers.length; },
    clearTimeout() {},
    setInterval() { return 1; },
    clearInterval() {},
    onMedicineOcrEvent(event) { events.push(event); },
  };
  delete require.cache[providerPath];
  require(providerPath);
  return {
    provider: global.window.MedicineBrowserOcr,
    input, listeners, events, timers, workers,
    clicked: () => clicked,
  };
}

function command(provider, name, operationId = null) {
  provider.postMessage(JSON.stringify({
    command: name, schema_version: 1,
    ...(operationId ? { operation_id: operationId } : {}),
  }));
}

test("uses one local direct ONNX worker and emits structured review data", async () => {
  const h = harness();
  command(h.provider, "get_capabilities");
  assert.equal(h.events.at(-1).capabilities.provider, "direct-onnx-wasm-cpu");
  assert.equal(h.events.at(-1).capabilities.backend, "wasm");

  command(h.provider, "start_scan", "browser-1");
  assert.equal(h.clicked(), 1);
  const selected = { name: "prescription.png" };
  h.input.files = [selected];
  h.listeners.change();
  await tick();

  assert.equal(h.workers.length, 1);
  const worker = h.workers[0];
  assert.equal(worker.url, "/ocr-assets/direct/ocr-worker.js");
  assert.deepEqual(worker.options, { type: "classic" });
  assert.equal(worker.messages.length, 1);
  assert.equal(worker.messages[0].type, "recognize");
  assert.equal(worker.messages[0].image, selected);

  worker.emit({ type: "progress", progress: 55 });
  worker.emit({ type: "result", items: [
    { text: "약명: 타이레놀정", score: 0.99 },
    { text: "1정 1일 2회 7일", score: 0.98 },
  ] });
  await tick();

  const review = h.events.find((event) => event.state === "review_required");
  assert.deepEqual(review.product_queries, ["타이레놀정"]);
  assert.equal(review.rows.length, 1);
  assert.equal(review.rows[0].product_query, "타이레놀정");
  assert.equal(review.rows[0].dose_amount, 1);
  assert.equal(review.rows[0].frequency_per_day, 2);
  assert.equal(review.rows[0].prescription_days, 7);
  assert.equal(JSON.stringify(review).includes("1정 1일"), false);
  assert.ok(h.events.some((event) => event.state === "recognizing" && event.progress === 55));
  assert.equal(worker.terminated, true);
  assert.equal(h.input.value, "");
});

test("cancel immediately terminates the worker and rejects stale output", async () => {
  const h = harness();
  command(h.provider, "start_scan", "browser-2");
  h.input.files = [{ name: "prescription.png" }];
  h.listeners.change();
  await tick();
  const worker = h.workers[0];

  command(h.provider, "cancel_scan", "browser-2");
  worker.emit({ type: "result", items: [{ text: "약명: 뒤늦은약정" }] });
  await tick();

  assert.equal(worker.terminated, true);
  assert.ok(h.events.some((event) => event.state === "cancelled"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
});

test("deadline terminates in-flight recognition", async () => {
  const h = harness();
  command(h.provider, "start_scan", "browser-3");
  h.input.files = [{ name: "slow.png" }];
  h.listeners.change();
  await tick();
  const worker = h.workers[0];
  await h.timers[0]();
  await tick();

  assert.equal(worker.terminated, true);
  assert.ok(h.events.some((event) => event.state === "expired"));
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

test("worker failure is explicit and does not fall back", async () => {
  const h = harness();
  command(h.provider, "start_scan", "browser-failed");
  h.input.files = [{ name: "broken.png" }];
  h.listeners.change();
  await tick();
  h.workers[0].fail("model unavailable");
  await tick();

  assert.ok(h.events.some((event) => event.state === "failed"));
  assert.equal(h.events.some((event) => event.state === "review_required"), false);
  assert.equal(h.workers[0].terminated, true);
});


test("preserves row geometry long enough to emit separate structured medication rows", async () => {
  const h = harness();
  command(h.provider, "start_scan", "browser-multi");
  h.input.files = [{ name: "multi.png" }];
  h.listeners.change();
  await tick();
  const box = (text, x1, y1, x2, y2) => ({ text, score: 0.99, poly: [[x1,y1],[x2,y1],[x2,y2],[x1,y2]] });
  h.workers[0].emit({ type: "result", items: [
    box("약품명", 10, 10, 140, 30), box("1회 투약량", 180, 10, 270, 30),
    box("1일 투여횟수", 300, 10, 410, 30), box("총투약일수", 450, 10, 550, 30),
    box("타이레놀정", 10, 50, 150, 70), box("1정", 190, 50, 240, 70),
    box("2회", 330, 50, 380, 70), box("7일", 470, 50, 520, 70),
    box("이부프로펜정", 10, 85, 160, 105), box("2정", 190, 85, 240, 105),
    box("3회", 330, 85, 380, 105), box("5일", 470, 85, 520, 105),
  ] });
  await tick();

  const review = h.events.find((event) => event.state === "review_required");
  assert.deepEqual(review.product_queries, ["타이레놀정", "이부프로펜정"]);
  assert.deepEqual(review.rows.map((row) => [row.product_query, row.dose_amount, row.frequency_per_day, row.prescription_days]), [
    ["타이레놀정", 1, 2, 7], ["이부프로펜정", 2, 3, 5],
  ]);
  assert.equal(JSON.stringify(review).includes("poly"), false);
});
