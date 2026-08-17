"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticDir = path.join(__dirname, "../../medicine_app/static");

function runStatic(name, context) {
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(staticDir, name), "utf8"), context);
  return context;
}

function prescriptionContext() {
  return runStatic("prescription.js", {});
}

function appContext(fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({}) })) {
  const toastNode = {
    textContent: "",
    classList: { add() {}, remove() {}, toggle() {} },
  };
  const document = {
    visibilityState: "visible",
    querySelector(selector) { return selector === "#toast" ? toastNode : null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: {
      MedicineLocalApi: null,
      location: { search: "" },
      addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    friendlyErrorMessage: () => "요청을 처리하지 못했어요",
    bindPeopleEvents() {},
    console,
    Intl,
    Date,
    URLSearchParams,
    crypto,
    fetch: fetchImpl,
    setTimeout: () => 1,
    clearTimeout() {},
    setInterval: () => 1,
  };
  runStatic("app.js", context);
  return { context, toastNode };
}

test("known backend validation stays friendly while unknown internal details are hidden", () => {
  const context = prescriptionContext();

  assert.equal(
    context.friendlyErrorMessage("frequency_per_day must match the number of schedule_times"),
    "하루 복용 횟수와 입력한 복용 시간 개수가 같아야 해요.",
  );
  assert.equal(context.friendlyErrorMessage("name is required"), "이름을 입력해주세요.");
  assert.equal(context.friendlyErrorMessage("unexpected server error"), "요청을 처리하지 못했어요");
  assert.equal(context.friendlyErrorMessage("native bridge failure"), "요청을 처리하지 못했어요");
  assert.equal(context.friendlyErrorMessage("personal data encryption failure"), "요청을 처리하지 못했어요");
});

test("native bridge never falls back to raw backend detail when the mapper is unavailable", () => {
  const window = {
    MedicineNative: {
      request() {
        return JSON.stringify({ status: 500, body: { detail: "native bridge failure" } });
      },
    },
  };
  const context = runStatic("native-api.js", { window, console });

  assert.throws(
    () => context.window.MedicineLocalApi.request("/api/health"),
    (error) => error.message === "요청을 처리하지 못했어요" && error.status === 500,
  );
});

test("ordinary user-facing toast copy is not rewritten by backend error sanitization", () => {
  const { context, toastNode } = appContext();

  context.toast("복용을 종료했어요");

  assert.equal(toastNode.textContent, "복용을 종료했어요");
});

test("transport failures become a generic user-facing request error", async () => {
  const { context } = appContext(async () => { throw new Error("Failed to fetch internal-host:8000"); });

  await assert.rejects(
    context.api("/api/health"),
    (error) => error.message === "요청을 처리하지 못했어요",
  );
});

test("changed error-handling assets use fresh cache keys", () => {
  const index = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");

  assert.match(index, /native-api\.js\?v=20260817err1/);
  assert.match(index, /prescription\.js\?v=20260817ux8/);
  assert.match(index, /app\.js\?v=20260817ux7/);
});
