"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function appContext() {
  const home = {
    innerHTML: "",
    querySelectorAll() { return []; },
  };
  const document = {
    querySelector(selector) { return selector === "#home-content" ? home : null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: { MedicineLocalApi: null, location: { search: "" } },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    profileMeta: () => "만 36세 · 남성",
    MedicineOcr: { getReview: () => null, cancel() {}, toggle() {}, init() {}, renderState() {} },
    bindPeopleEvents() {},
    friendlyErrorMessage: (value) => String(value),
    console,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    crypto,
    fetch: async () => { throw new Error("unexpected fetch"); },
  };
  vm.createContext(context);
  const source = fs.readFileSync(
    path.join(__dirname, "../../medicine_app/static/app.js"), "utf8",
  );
  vm.runInContext(source, context);
  return { context, home };
}

test("home surfaces active medications whose schedule information is missing", () => {
  const { context, home } = appContext();
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      medications: [{ id: "m1", product_name: "일정없는약" }],
      daily_plan: {
        doses: [],
        prn_medications: [],
        unscheduled_medications: [{ id: "m1", product_name: "일정없는약" }],
        summary: {},
      },
    };
    renderHome();
  `, context);

  assert.match(home.innerHTML, /일정없는약/);
  assert.match(home.innerHTML, /일정 정보 미입력/);
  assert.doesNotMatch(home.innerHTML, /오늘 예정된 복용이 없어요/);
});
