"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticRoot = path.join(__dirname, "../../medicine_app/static");
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
  return { context, sheet, focusTarget, first, last, backdrop, shell, events };
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
  vm.runInContext(source("app.js"), context);
  return { context, meds, search, title, sourceButton };
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

test("shared UI owns search clear and medication-stop confirmation controls", () => {
  const index = source("index.html");
  const app = source("app.js");
  const styles = source("styles.css");
  assert.match(index, /id="drug-query-clear"[^>]*aria-label="검색어 지우기"/);
  assert.match(styles, /::-webkit-search-cancel-button/);
  assert.match(styles, /\.search-clear\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/s);
  assert.match(app, /#drug-query-clear/);
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

test("successful modal flows defer focus restoration until after rerender", () => {
  const people = source("people.js");
  const prescription = source("prescription.js");
  assert.match(people, /closeSheets\(\{\s*restoreFocus:\s*false\s*\}\)/);
  assert.match(people, /showScreen\([^\n]+\{\s*focus:\s*true\s*\}/);
  assert.match(prescription, /closeSheets\(\{\s*restoreFocus:\s*false\s*\}\)/);
  assert.match(prescription, /showScreen\("meds",\s*\{\s*focus:\s*true\s*\}\)/);
});
