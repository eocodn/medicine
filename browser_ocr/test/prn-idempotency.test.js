"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function prescriptionContext(storage = new Map()) {
  const context = {
    document: { querySelector() { return null; }, querySelectorAll() { return []; } },
    window: {},
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    console,
    crypto,
    Date,
    setTimeout,
    clearTimeout,
    reconcileDoseMutation() {},
    renderAll() {},
    toast() {},
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../medicine_app/static/app-state.js"), "utf8"),
    context,
  );
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../medicine_app/static/dose-actions.js"), "utf8"),
    context,
  );
  return context;
}

test("PRN ambiguous failure retries the same logical request id", async () => {
  const context = prescriptionContext();
  const calls = [];
  let attempt = 0;
  context.api = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    attempt += 1;
    if (attempt === 1) {
      const error = new Error("ambiguous failure");
      error.status = 500;
      throw error;
    }
    return { id: "dose-1", status: "taken", recent_logs: [] };
  };

  await vm.runInContext(`recordPrnIntake("med-1")`, context);
  await vm.runInContext(`recordPrnIntake("med-1")`, context);

  assert.equal(calls.length, 2);
  assert.ok(calls[0].body.request_id);
  assert.equal(calls[1].body.request_id, calls[0].body.request_id);
  assert.equal(vm.runInContext(`state.prnRequests.size`, context), 0);
});

test("PRN definitive client error releases the request id for a new logical attempt", async () => {
  const context = prescriptionContext();
  const calls = [];
  context.api = async (url, options) => {
    calls.push(JSON.parse(options.body));
    if (calls.length === 1) {
      const error = new Error("daily maximum");
      error.status = 400;
      throw error;
    }
    return { id: "dose-2", status: "taken", recent_logs: [] };
  };

  await vm.runInContext(`recordPrnIntake("med-1")`, context);
  await vm.runInContext(`recordPrnIntake("med-1")`, context);

  assert.equal(calls.length, 2);
  assert.notEqual(calls[1].request_id, calls[0].request_id);
});

test("PRN ambiguous failure keeps the request id across WebView recreation", async () => {
  const storage = new Map();
  const firstContext = prescriptionContext(storage);
  let firstRequestId = null;
  firstContext.api = async (url, options) => {
    firstRequestId = JSON.parse(options.body).request_id;
    const error = new Error("ambiguous failure");
    error.status = 500;
    throw error;
  };

  await vm.runInContext(`recordPrnIntake("med-recreate")`, firstContext);

  const secondContext = prescriptionContext(storage);
  let retriedRequestId = null;
  secondContext.api = async (url, options) => {
    retriedRequestId = JSON.parse(options.body).request_id;
    return { id: "dose-recreated", status: "taken", recent_logs: [] };
  };
  await vm.runInContext(`recordPrnIntake("med-recreate")`, secondContext);

  assert.equal(retriedRequestId, firstRequestId);
  assert.equal(storage.has("medicine.prnRequest.med-recreate"), false);
});

test("dashboard reconciliation clears a committed PRN request before the next intake", async () => {
  const storage = new Map();
  const firstContext = prescriptionContext(storage);
  let committedRequestId = null;
  firstContext.api = async (url, options) => {
    committedRequestId = JSON.parse(options.body).request_id;
    const error = new Error("response lost after commit");
    error.status = 500;
    throw error;
  };
  await vm.runInContext(`recordPrnIntake("med-committed")`, firstContext);

  const secondContext = prescriptionContext(storage);
  secondContext.committedRequestId = committedRequestId;
  vm.runInContext(`reconcilePrnRequestIds([{
    medication_id: "med-committed",
    request_id: committedRequestId,
  }])`, secondContext);

  let nextRequestId = null;
  secondContext.api = async (url, options) => {
    nextRequestId = JSON.parse(options.body).request_id;
    return { id: "dose-next", status: "taken", recent_logs: [] };
  };
  await vm.runInContext(`recordPrnIntake("med-committed")`, secondContext);

  assert.notEqual(nextRequestId, committedRequestId);
});