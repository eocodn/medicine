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
  const medications = { innerHTML: "", querySelectorAll() { return []; } };
  const history = { innerHTML: "", querySelectorAll() { return []; } };
  const document = {
    querySelector(selector) {
      if (selector === "#home-content") return home;
      if (selector === "#medications-list") return medications;
      if (selector === "#dose-history") return history;
      return null;
    },
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
  const stateSource = fs.readFileSync(
    path.join(__dirname, "../../medicine_app/static/app-state.js"), "utf8",
  );
  const source = fs.readFileSync(
    path.join(__dirname, "../../medicine_app/static/app.js"), "utf8",
  );
  vm.runInContext(stateSource, context);
  vm.runInContext(source, context);
  return { context, home, medications, history };
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


test("stale dashboard state suppresses stale medication actions after a committed mutation", () => {
  const { context, medications, history } = appContext();
  vm.runInContext(`
    state.dashboard = { medications: [{ id: "m1", product_name: "이미 종료된 약", revision: 1, active: true }] };
    state.dashboardStale = true;
    renderMedications();
  `, context);

  assert.match(medications.innerHTML, /변경사항은 저장됐어요/);
  assert.doesNotMatch(medications.innerHTML, /이미 종료된 약|data-stop|data-edit/);
  assert.equal(history.innerHTML, "");
});
