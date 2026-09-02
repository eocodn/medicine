"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticRoot = path.join(__dirname, "../../ui/dist");
const source = (name) => fs.readFileSync(path.join(staticRoot, name), "utf8");

function classList(initial = []) {
  const values = new Set(initial);
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
    toggle(value, force) {
      const enabled = force === undefined ? !values.has(value) : Boolean(force);
      if (enabled) values.add(value); else values.delete(value);
      return enabled;
    },
  };
}

function dialogContext({ open = false } = {}) {
  const backdrop = { classList: classList(open ? [] : ["hidden"]) };
  const shell = { inert: open };
  const focusTarget = {
    tabIndex: -1,
    focus() { context.document.activeElement = this; },
  };
  const pageTitle = {
    tabIndex: -1,
    focus() { context.document.activeElement = this; },
  };
  const first = { classList: classList(), focus() { context.document.activeElement = this; } };
  const last = { classList: classList(), focus() { context.document.activeElement = this; } };
  const sheet = {
    id: "risk-sheet",
    classList: classList(open ? [] : ["hidden"]),
    querySelector(selector) { return selector === "[data-sheet-focus]" ? focusTarget : null; },
    querySelectorAll() { return [first, last]; },
    focus() { context.document.activeElement = this; },
  };
  const events = [];
  const context = {
    document: {
      activeElement: null,
      querySelector(selector) {
        if (selector === "#sheet-backdrop") return backdrop;
        if (selector === ".app-shell") return shell;
        if (selector === "#risk-sheet") return sheet;
        if (selector === "#page-title") return pageTitle;
        return null;
      },
      querySelectorAll(selector) { return selector === ".bottom-sheet" ? [sheet] : []; },
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent(event) { events.push(event.type); },
    },
    CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    setTimeout(callback) { callback(); },
    window: {},
  };
  vm.createContext(context);
  vm.runInContext(source("dialog.js"), context);
  return { context, sheet, focusTarget, pageTitle, first, last, backdrop, shell, events };
}

test("native back is consumed only when an app-owned sheet is open", () => {
  const closed = dialogContext({ open: false });
  assert.equal(closed.context.handleNativeBack(), false);
  assert.equal(closed.sheet.classList.contains("hidden"), true);

  const opened = dialogContext({ open: true });
  assert.equal(opened.context.handleNativeBack(), true);
  assert.equal(opened.sheet.classList.contains("hidden"), true);
  assert.equal(opened.shell.inert, false);
});

test("modal rerender focus helper targets a non-input sheet landmark", () => {
  const { context, sheet, focusTarget } = dialogContext({ open: true });
  assert.equal(context.focusSheetContent(sheet), true);
  assert.equal(context.document.activeElement, focusTarget);
});

test("modal focus trap contains reverse tabbing from the programmatic heading", () => {
  const { context, focusTarget, last } = dialogContext({ open: true });
  context.document.activeElement = focusTarget;
  let prevented = false;
  context.trapSheetFocus({
    key: "Tab",
    shiftKey: true,
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(context.document.activeElement, last);
});


test("successful modal completion establishes stable focus before any refresh", () => {
  const { context, pageTitle, sheet } = dialogContext({ open: true });
  context.closeSheetsAfterMutation();
  assert.equal(sheet.classList.contains("hidden"), true);
  assert.equal(context.document.activeElement, pageTitle);
});

test("ordinary modal dismissal falls back when its opener was rerendered away", () => {
  const { context, pageTitle } = dialogContext({ open: false });
  const opener = {
    isConnected: true,
    focus() { context.document.activeElement = this; },
  };
  context.document.activeElement = opener;
  context.openSheet("#risk-sheet");
  opener.isConnected = false;
  context.closeSheets();
  assert.equal(context.document.activeElement, pageTitle);
});

function screenContext() {
  const nodes = {};
  const makeScreen = (name, active = false) => ({
    dataset: { screen: name },
    classList: classList(active ? ["screen", "active"] : ["screen"]),
    contains(node) { return node?.screen === this; },
  });
  const meds = makeScreen("meds", true);
  const search = makeScreen("search");
  const home = makeScreen("home");
  const people = makeScreen("people");
  const screens = [home, meds, search, people];
  const navs = ["home", "meds", "search", "people"].map((name) => ({
    dataset: { nav: name }, classList: classList(name === "meds" ? ["active"] : []),
    setAttribute() {}, removeAttribute() {},
  }));
  const title = {
    textContent: "복용 관리",
    tabIndex: -1,
    focus() { context.document.activeElement = this; },
  };
  const sourceButton = { screen: meds };
  const searchQuery = {
    focusCount: 0,
    focus() { this.focusCount += 1; context.document.activeElement = this; },
  };
  nodes["#drug-query"] = searchQuery;
  const context = {
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    document: {
      activeElement: sourceButton,
      visibilityState: "visible",
      querySelector(selector) {
        if (selector === ".screen.active") return screens.find((node) => node.classList.contains("active")) || null;
        if (selector === "#page-title") return title;
        return nodes[selector] || null;
      },
      querySelectorAll(selector) {
        if (selector === ".screen") return screens;
        if (selector === ".nav-item") return navs;
        return [];
      },
      addEventListener() {},
    },
    window: { addEventListener() {}, location: { search: "" }, MedicineLocalApi: null },
    console,
    URLSearchParams,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    setInterval() {},
    crypto,
  };
  vm.createContext(context);
  vm.runInContext(source("dialog.js"), context);
  vm.runInContext(source("app-state.js"), context);
  vm.runInContext(source("app.js"), context);
  return { context, meds, search, title, sourceButton, searchQuery };
}

test("screen navigation moves focus out of a source screen that becomes hidden", () => {
  const { context, search, title } = screenContext();
  context.showScreen("search");
  assert.equal(search.classList.contains("active"), true);
  assert.equal(context.document.activeElement, title);
});

test("explicit programmatic navigation can focus the stable page landmark", () => {
  const { context, title } = screenContext();
  context.document.activeElement = null;
  context.showScreen("home", { focus: true });
  assert.equal(context.document.activeElement, title);
});


test("search box chrome focuses the input but clear-button taps do not", () => {
  const { context, searchQuery } = screenContext();
  const chromeTarget = { closest() { return null; } };
  context.focusSearchFromBox({ target: chromeTarget });
  assert.equal(searchQuery.focusCount, 1);

  const clearTarget = { closest(selector) { return selector === "#drug-query-clear" ? this : null; } };
  context.focusSearchFromBox({ target: clearTarget });
  assert.equal(searchQuery.focusCount, 1);
});

test("shared UI owns search clear and medication-stop confirmation controls", () => {
  const index = source("index.html");
  const app = source("app.js");
  const styles = source("styles.css");
  assert.match(index, /id="drug-query-clear"[^>]*aria-label="검색어 지우기"/);
  assert.match(styles, /::-webkit-search-cancel-button/);
  assert.match(styles, /\.search-clear\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/s);
  assert.match(app, /#drug-query-clear/);
  assert.match(app, /\.search-box"\)\.addEventListener\("click", focusSearchFromBox\)/);
  assert.match(app, /dispatchEvent\(new Event\("input"/);
  assert.match(index, /id="stop-medication-sheet"/);
  assert.match(index, /id="confirm-stop-medication"/);
  assert.doesNotMatch(app, /\bconfirm\(/);
  assert.match(app, /openSheet\("#stop-medication-sheet"\)/);
});

test("prescription rerenders explicitly restore modal focus", () => {
  const prescription = source("prescription.js");
  assert.match(prescription, /focusSheetContent/);
  assert.match(prescription, /data-sheet-focus/);
  assert.match(prescription, /data-remove-schedule-time[\s\S]{0,900}\.focus\(/);
});

test("successful modal flows establish stable focus before authoritative refresh", () => {
  const app = source("app.js");
  const people = source("people.js");
  const prescription = source("prescription.js");
  assert.equal((people.match(/closeSheetsAfterMutation\(\)/g) || []).length, 2);
  assert.equal((app.match(/closeSheetsAfterMutation\(\)/g) || []).length, 1);
  assert.equal((prescription.match(/closeSheetsAfterMutation\(\)/g) || []).length, 2);
  assert.match(people, /showScreen\([^\n]+\{\s*focus:\s*true\s*\}/);
  assert.match(prescription, /showScreen\("meds",\s*\{\s*focus:\s*true\s*\}\)/);
});


test("committed modal mutations distinguish refresh-only failure from mutation failure", () => {
  const app = source("app.js");
  const people = source("people.js");
  const prescription = source("prescription.js");

  const appState = source("app-state.js");
  assert.match(app, /reconcileCommittedMedication\(stopped\)/);
  assert.match(app, /dashboard refresh after medication stop failed/);
  assert.match(app, /복용은 종료됐지만 목록을 새로고침하지 못했어요/);
  assert.match(appState, /function markDashboardStale/);
  assert.match(appState, /function reconcileCommittedMedication/);
  assert.doesNotMatch(appState, /function committedMedicationAssessmentFlags/);

  assert.equal((people.match(/closeSheetsAfterMutation\(\)/g) || []).length, 2);
  assert.match(people, /people refresh after profile save failed/);
  assert.match(people, /프로필은 저장됐지만 화면을 새로고침하지 못했어요/);
  assert.match(people, /people refresh after profile delete failed/);
  assert.match(people, /프로필은 삭제됐지만 화면을 새로고침하지 못했어요/);

  assert.match(prescription, /reconcileCommittedMedication\(updated\)/);
  assert.match(prescription, /dashboard refresh after medication edit failed/);
  assert.match(prescription, /약은 수정됐지만 목록을 새로고침하지 못했어요/);
});

function committedMutationContext() {
  const events = [];
  const storage = new Map();
  const context = {
    localStorage: {
      getItem() { return null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    document: {
      activeElement: null,
      visibilityState: "visible",
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {},
    },
    window: { addEventListener() {}, location: { search: "" }, MedicineLocalApi: null, MedicineOcrIntake: null },
    console: { ...console, error() {} },
    URLSearchParams,
    Intl,
    Date,
    setTimeout,
    clearTimeout,
    setInterval() {},
    crypto,
    CSS: { escape(value) { return String(value); } },
    CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    FormData: class FormData {
      constructor(form) { this.form = form; }
      entries() { return Object.entries(this.form.formData || {})[Symbol.iterator](); }
    },
  };
  vm.createContext(context);
  vm.runInContext(source("dialog.js"), context);
  vm.runInContext(source("app-state.js"), context);
  vm.runInContext(source("app.js"), context);
  vm.runInContext(source("people.js"), context);
  vm.runInContext(source("prescription.js"), context);
  context.closeSheetsAfterMutation = () => events.push("close");
  context.renderAll = () => events.push("render");
  context.showScreen = (name) => events.push(`screen:${name}`);
  context.toast = (message) => events.push(`toast:${message}`);
  context.syncBirthDateFields = () => {};
  context.todayInKorea = () => "2026-08-19";
  context.prescriptionPayloadFromForm = () => ({ dose_amount: 2, dose_unit: "정", frequency_per_day: 1 });
  return {
    context,
    events,
    storage,
    setState(expression) { vm.runInContext(expression, context); },
    read(expression) { return vm.runInContext(expression, context); },
  };
}

test("profile edit keeps the committed person when refresh fails", async () => {
  const harness = committedMutationContext();
  harness.setState(`
    state.people = [{ id: "p1", name: "이전 이름", birth_date: "1990-01-01", age: 36, sex: "male", pregnancy_status: "not_applicable", lactation_status: "not_applicable" }];
    state.currentPersonId = "p1";
    state.dashboard = { person: { id: "p1" }, medications: [] };
    state.editingPersonId = "p1";
  `);
  harness.context.api = async () => ({
    id: "p1", name: "새 이름", birth_date: "1990-01-01", age: 36, sex: "male",
    pregnancy_status: "not_applicable", lactation_status: "not_applicable", profile_needs_review: false,
  });
  harness.context.loadPeople = async () => { throw new Error("refresh failed"); };
  const form = {
    formData: { name: "새 이름", birth_date: "1990-01-01", sex: "male" },
    reset() {},
  };

  await harness.context.submitPerson({ preventDefault() {}, currentTarget: form });

  assert.equal(harness.read("state.people[0].name"), "새 이름");
  assert.equal(harness.read("state.dashboardStale"), true);
  assert.ok(harness.events.includes("screen:people"));
});

test("profile create selects the committed person without retaining the previous dashboard", async () => {
  const harness = committedMutationContext();
  harness.setState(`
    state.people = [{ id: "p1", name: "기존", birth_date: "1990-01-01", age: 36, sex: "male" }];
    state.currentPersonId = "p1";
    state.dashboard = { person: { id: "p1" }, medications: [{ id: "old-med" }] };
    state.editingPersonId = null;
  `);
  harness.context.api = async () => ({
    id: "p2", name: "신규", birth_date: "2000-01-01", age: 26, sex: "male",
    pregnancy_status: "not_applicable", lactation_status: "not_applicable", profile_needs_review: false,
  });
  harness.context.loadPeople = async () => { throw new Error("refresh failed"); };
  const form = {
    formData: { name: "신규", birth_date: "2000-01-01", sex: "male" },
    reset() {},
  };

  await harness.context.submitPerson({ preventDefault() {}, currentTarget: form });

  assert.equal(harness.read("state.currentPersonId"), "p2");
  assert.equal(harness.read("state.people.some((person) => person.id === 'p2')"), true);
  assert.equal(harness.read("state.dashboard === null"), true);
  assert.equal(harness.read("state.dashboardStale"), true);
});

test("profile delete removes the committed person locally before refresh", async () => {
  const harness = committedMutationContext();
  harness.setState(`
    state.people = [
      { id: "p1", name: "삭제", birth_date: "1990-01-01", age: 36, sex: "male" },
      { id: "p2", name: "유지", birth_date: "1991-01-01", age: 35, sex: "male" }
    ];
    state.currentPersonId = "p1";
    state.dashboard = { person: { id: "p1" }, medications: [{ id: "m1" }] };
    state.pendingDeletePersonId = "p1";
  `);
  harness.context.api = async () => ({ id: "p1", deleted: true });
  harness.context.loadPeople = async () => { throw new Error("refresh failed"); };

  await harness.context.confirmDeletePerson();

  assert.equal(harness.read("state.people.some((person) => person.id === 'p1')"), false);
  assert.equal(harness.read("state.currentPersonId"), "p2");
  assert.equal(harness.read("state.dashboard === null"), true);
  assert.equal(harness.read("state.dashboardStale"), true);
});

test("medication edit keeps authoritative revision and regimen when refresh fails", async () => {
  const harness = committedMutationContext();
  harness.setState(`
    state.dashboard = { medications: [{ id: "m1", revision: 1, active: true, dosage_text: "1정", product_name: "약", dur_alert: true }] };
    state.editingMedicationId = "m1";
  `);
  harness.context.api = async () => ({
    id: "m1", revision: 2, active: true, dosage_text: "2정", product_name: "약",
    schedules: [], assessment: { dur_checks: [], requires_review: false },
  });
  harness.context.loadDashboard = async () => { throw new Error("refresh failed"); };

  await harness.context.confirmEditMedication();

  assert.equal(harness.read("state.dashboard.medications[0].revision"), 2);
  assert.equal(harness.read("state.dashboard.medications[0].dosage_text"), "2정");
  assert.equal(harness.read("state.dashboardStale"), true);
  assert.ok(harness.events.includes("screen:meds"));
});

test("medication stop removes the committed inactive medication before refresh", async () => {
  const harness = committedMutationContext();
  harness.setState(`
    state.dashboard = { medications: [{ id: "m1", revision: 1, active: true, dosage_text: "1정", product_name: "약" }] };
    state.pendingStopMedicationId = "m1";
  `);
  harness.context.api = async () => ({ id: "m1", revision: 2, active: false, product_name: "약", schedules: [] });
  harness.context.loadDashboard = async () => { throw new Error("refresh failed"); };

  await harness.context.confirmStopMedication();

  assert.equal(harness.read("state.dashboard.medications.length"), 0);
  assert.equal(harness.read("state.dashboardStale"), true);
  assert.ok(harness.events.includes("screen:meds"));
});
