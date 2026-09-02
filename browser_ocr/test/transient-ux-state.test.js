"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticRoot = path.join(__dirname, "../../ui/dist");

function source(name) {
  return fs.readFileSync(path.join(staticRoot, name), "utf8");
}

function productSearchContext() {
  const query = { value: "씬지록신 25" };
  const status = { textContent: "" };
  const results = { innerHTML: "", querySelectorAll() { return []; } };
  const hero = { classList: { toggle() {} } };
  const document = {
    querySelector(selector) {
      return {
        "#drug-query": query,
        "#search-status": status,
        "#drug-results": results,
        ".search-hero": hero,
      }[selector] || null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: { MedicineLocalApi: null, MedicineOcrIntake: { reset() {} } },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    friendlyErrorMessage: (value) => String(value),
    console,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    fetch: async () => { throw new Error("unexpected fetch"); },
  };
  vm.createContext(context);
  vm.runInContext(source("app-state.js"), context);
  vm.runInContext(source("app.js"), context);
  vm.runInContext(source("product-search.js"), context);
  return { context, query, status, results };
}

test("leaving Search clears the OCR product discovery session", () => {
  const app = source("app.js");
  const intake = source("ocr-intake.js");

  assert.match(app, /function resetOcrProductDiscovery/);
  assert.match(app, /previousScreen === "search"[\s\S]{0,260}resetOcrProductDiscovery/);
  assert.match(app, /MedicineOcrIntake\?\.reset\?\.\(\)/);
  assert.match(intake, /function reset\(/);
});

test("OCR product discovery carries no regimen draft or profile-bound parser state", () => {
  const app = source("app.js");
  const state = source("app-state.js");
  const prescription = source("prescription.js");

  assert.doesNotMatch(app, /pendingParser|parserDraft|uncertainty/i);
  assert.doesNotMatch(state, /pendingParser|parserDraft|uncertainty/i);
  assert.doesNotMatch(prescription, /pendingParser|parserDraft|uncertainty|applyParserDraft/i);
});

test("visible OCR import control mirrors disabled and busy state", () => {
  const intake = source("ocr-intake.js");
  const styles = source("styles.css");

  assert.match(intake, /\.ocr-file-button/);
  assert.match(intake, /aria-disabled/);
  assert.match(intake, /is-disabled/);
  assert.match(styles, /\.ocr-file-button\.is-disabled/);
});

test("profile form close discards edit-session state and every add path reinitializes", () => {
  const app = source("app.js");
  const people = source("people.js");
  const dialog = source("dialog.js");

  assert.match(app, /home-add-person[\s\S]{0,180}openPersonForm\(\)/);
  assert.match(dialog, /medicine:sheet-closed/);
  assert.match(people, /medicine:sheet-closed[\s\S]{0,320}editingPersonId = null/);
  assert.match(people, /medicine:sheet-closed[\s\S]{0,320}form\.reset\(\)/);
});

test("editing a drug query immediately removes stale clickable search results", () => {
  const app = source("app.js");

  assert.match(app, /#drug-query[\s\S]{0,520}#search-status[\s\S]{0,180}textContent = ""[\s\S]{0,220}#drug-results[\s\S]{0,180}innerHTML = ""[\s\S]{0,260}setTimeout\(runDrugSearch, 280\)/);
});

test("editing a drug query invalidates any in-flight search before debounce", () => {
  const app = source("app.js");

  assert.match(app, /function invalidateProductSearch\(\)[\s\S]{0,220}state\.searchRequestId \+= 1[\s\S]{0,220}clearTimeout\(state\.searchTimer\)/);
  assert.match(app, /#drug-query[\s\S]{0,520}addEventListener\("input"[\s\S]{0,220}invalidateProductSearch\(\)[\s\S]{0,420}setTimeout\(runDrugSearch, 280\)/);
});

test("OCR candidate query replacement clears stale clickable results before async search", () => {
  const app = source("app.js");
  assert.match(
    app,
    /medicine:ocr-result[\s\S]{0,1000}MedicineOcrIntake\?\.discoverMedicationRows[\s\S]{0,1000}startNextOcrProductSearch/,
  );
  assert.match(
    app,
    /function startNextOcrProductSearch[\s\S]{0,900}#drug-query[\s\S]{0,220}value = query[\s\S]{0,320}#drug-results[\s\S]{0,220}innerHTML = ""[\s\S]{0,420}runDrugSearch/,
  );
});

test("completing the final OCR row clears its search UI and state", () => {
  const { context, query, status, results } = productSearchContext();
  query.value = "타이레놀정";
  status.textContent = "인식된 약 · 제품 후보를 선택해주세요.";
  results.innerHTML = '<article data-product-select="123">타이레놀정</article>';
  vm.runInContext(`
    state.activeOcrProductRow = { row_id: "row-1", product_query: "타이레놀정" };
    state.pendingOcrProductRows = [];
    state.ocrProductRowTotal = 1;
    state.ocrProductRowIndex = 1;
  `, context);

  assert.equal(vm.runInContext("completeOcrProductRowAndContinue()", context), false);
  assert.equal(query.value, "");
  assert.equal(status.textContent, "");
  assert.equal(results.innerHTML, "");
  assert.equal(vm.runInContext("state.activeOcrProductRow", context), null);
  assert.equal(vm.runInContext("state.ocrProductRowTotal", context), 0);
  assert.equal(vm.runInContext("state.ocrProductRowIndex", context), 0);
});

test("editing a query rejects an already in-flight search response", async () => {
  const { context, query, results } = productSearchContext();
  let resolveRequest;
  let requestPath = "";
  context.window.MedicineLocalApi = {
    request(path) {
      requestPath = path;
      return new Promise((resolve) => { resolveRequest = resolve; });
    },
  };

  const pending = vm.runInContext("runDrugSearch()", context);
  assert.doesNotMatch(requestPath, /mode=/);

  query.value = "씬지록신 25 ";
  vm.runInContext("invalidateProductSearch();", context);
  resolveRequest({ items: [{ product_ref: "stale", product_name: "stale" }], has_more: false, next_offset: null });

  assert.equal(await pending, false);
  assert.equal(results.innerHTML, "");
});

test("product search appends the next offset page without reranking prior cards", async () => {
  const { context, query, results } = productSearchContext();
  const paths = [];
  context.window.MedicineLocalApi = {
    request(path) {
      paths.push(path);
      if (path.includes("offset=0")) {
        return Promise.resolve({
          items: [{ product_ref: "P1", product_name: "첫 제품", permit_status: "active" }],
          has_more: true,
          next_offset: 30,
        });
      }
      return Promise.resolve({
        items: [{ product_ref: "P2", product_name: "둘째 제품", permit_status: "active" }],
        has_more: false,
        next_offset: null,
      });
    },
  };

  assert.equal(await vm.runInContext("runDrugSearch()", context), true);
  const requestId = vm.runInContext("state.searchRequestId", context);
  assert.equal(await vm.runInContext(`loadMoreProductSearch(${JSON.stringify(query.value)}, ${requestId})`, context), true);
  assert.match(results.innerHTML, /첫 제품/);
  assert.match(results.innerHTML, /둘째 제품/);
  assert.match(paths[0], /limit=30&offset=0/);
  assert.match(paths[1], /limit=30&offset=30/);
  assert.equal(vm.runInContext("state.searchHasMore", context), false);
});
