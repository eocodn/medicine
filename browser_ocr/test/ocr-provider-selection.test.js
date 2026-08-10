"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const bridgePath = path.resolve(__dirname, "../../medicine_app/static/ocr.js");

function loadBridge({ native = null, browser = null } = {}) {
  const nodes = {
    "#ocr-scan-button": { disabled: true, textContent: "" },
    "#ocr-status": { textContent: "" },
  };
  global.window = {
    document: { querySelector: (selector) => nodes[selector] || null },
    addEventListener() {},
    crypto: { randomUUID: () => "operation-1" },
    MedicineNative: native,
    MedicineBrowserOcr: browser,
  };
  delete require.cache[bridgePath];
  require(bridgePath);
  return global.window.MedicineOcr;
}

test("uses browser provider when native bridge is absent", () => {
  const messages = [];
  const ocr = loadBridge({ browser: { postMessage: (message) => messages.push(JSON.parse(message)) } });
  ocr.init();
  assert.equal(messages[0].command, "get_capabilities");
  assert.equal(ocr.start(), true);
  assert.equal(messages[1].command, "start_scan");
  assert.equal(messages[1].operation_id, "operation-1");
});

test("keeps Android native bridge ahead of browser provider", () => {
  const nativeMessages = [];
  const browserMessages = [];
  const ocr = loadBridge({
    native: { postMessage: (message) => nativeMessages.push(JSON.parse(message)) },
    browser: { postMessage: (message) => browserMessages.push(JSON.parse(message)) },
  });
  ocr.init();
  ocr.start();
  assert.deepEqual(nativeMessages.map((message) => message.command), ["get_capabilities", "start_scan"]);
  assert.deepEqual(browserMessages, []);
});
