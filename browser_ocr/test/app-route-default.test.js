"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function appContext() {
  const nodes = new Map();
  for (const selector of [
    "#pending-dose-amount", "#pending-dose-unit", "#pending-frequency", "#pending-days",
    "#pending-times", "#pending-meal", "#pending-route", "#pending-start-date", "#pending-prn",
    "#confirm-add-med",
  ]) {
    nodes.set(selector, {
      value: "",
      checked: false,
      addEventListener() {},
    });
  }
  const risk = {
    _html: "",
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = value; },
    querySelector(selector) { return nodes.get(selector) || null; },
    querySelectorAll() { return []; },
  };
  const backdrop = { classList: { add() {}, remove() {} } };
  const document = {
    querySelector(selector) {
      if (selector === "#risk-sheet-content") return risk;
      if (selector === "#sheet-backdrop") return backdrop;
      return nodes.get(selector) || null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: { MedicineLocalApi: null, location: { search: "" } },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    profileMeta: () => "",
    MedicineOcr: {
      getReview: () => null,
      cancel() {},
      toggle() {},
      init() {},
      renderState() {},
      prefillForm() {},
    },
    bindPeopleEvents() {},
    friendlyErrorMessage: (value) => String(value),
    hasClearDurCoverage: () => true,
    durStatusHtml: () => "",
    console,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    crypto,
    fetch: async () => { throw new Error("unexpected fetch"); },
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../medicine_app/static/app.js"), "utf8"),
    context,
  );
  return { context, risk, nodes };
}

function preview(route) {
  return {
    product: { product_name: "경로제품", suggested_administration_route: route },
    person: { name: "사용자" },
    current_medication_count: 0,
    dur_checks: [],
  };
}

test("new medication uses an authoritative route hint", () => {
  const { context, risk, nodes } = appContext();

  context.renderRiskSheet(preview("injection"));

  assert.equal(nodes.get("#pending-route").value, "injection");
  assert.doesNotMatch(risk.innerHTML, /투여 경로를 확인해주세요/);
});

test("new medication stays unknown when route evidence is unavailable", () => {
  const { context, risk, nodes } = appContext();

  context.renderRiskSheet(preview("unknown"));

  assert.equal(nodes.get("#pending-route").value, "unknown");
  assert.match(risk.innerHTML, /투여 경로를 확인해주세요/);
});

test("conditional DUR items get a condition-review heading", () => {
  const { context, risk } = appContext();
  const conditional = preview("oral");
  conditional.dur_checks = [{
    category: "pregnancy_contraindication",
    status: "conditional",
  }];

  context.renderRiskSheet(conditional);

  assert.match(risk.innerHTML, /조건 확인이 필요한 DUR 항목 1건이 있어요/);
  assert.doesNotMatch(risk.innerHTML, /DUR 판정 결과를 확인할 수 없어요/);
});