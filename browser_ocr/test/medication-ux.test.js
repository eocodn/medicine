"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const staticDir = path.join(__dirname, "../../medicine_app/static");

function source(name) {
  return fs.readFileSync(path.join(staticDir, name), "utf8");
}

function prescriptionContext() {
  const nodes = new Map();
  const add = (selector, initial = {}) => nodes.set(selector, {
    value: "", checked: false, disabled: false, addEventListener() {}, ...initial,
  });
  add("#pending-dose-amount", { value: "1" });
  add("#pending-dose-unit", { value: "정" });
  add("#pending-frequency", { value: "3" });
  add("#pending-days", { value: "30" });
  add("#pending-times", { value: "08:00, 13:00, 19:00" });
  add("#pending-meal", { value: "after_meal" });
  add("#pending-route", { value: "oral" });
  add("#pending-start-date", { value: "2026-08-17" });
  add("#pending-prn", { checked: false });
  add("#pending-long-term", { checked: false });
  add("#pending-prn-max", { value: "2" });

  const context = {
    document: {},
    state: {},
    $: (selector) => nodes.get(selector) || null,
    $$: () => [],
    console,
  };
  vm.createContext(context);
  vm.runInContext(source("prescription.js"), context);
  return { context, nodes };
}

test("primary navigation keeps only user tasks and removes the empty settings destination", () => {
  const index = source("index.html");
  const navItems = [...index.matchAll(/class="nav-item[^\"]*" data-nav="([^"]+)"/g)].map((match) => match[1]);

  assert.deepEqual(navItems, ["home", "meds", "search", "people"]);
  assert.doesNotMatch(index, /data-screen="settings"/);
  assert.doesNotMatch(index, /data-nav="settings"/);
});

test("top bar exposes the active person's name instead of only an initial", () => {
  const index = source("index.html");
  const people = source("people.js");

  assert.match(index, /id="profile-shortcut-name"/);
  assert.match(index, /id="profile-shortcut-avatar"/);
  assert.match(people, /profile-shortcut-name/);
  assert.match(people, /person\.name/);
});

test("PRN and long-term toggles preserve values while disabling incompatible fields", () => {
  const { context, nodes } = prescriptionContext();
  const frequency = nodes.get("#pending-frequency");
  const times = nodes.get("#pending-times");
  const days = nodes.get("#pending-days");

  nodes.get("#pending-prn").checked = true;
  context.syncPrnFields();
  assert.equal(frequency.value, "3");
  assert.equal(times.value, "08:00, 13:00, 19:00");
  assert.equal(frequency.disabled, true);
  assert.equal(times.disabled, true);

  nodes.get("#pending-long-term").checked = true;
  context.syncLongTermFields();
  assert.equal(days.value, "30");
  assert.equal(days.disabled, true);
});

test("payload ignores preserved incompatible values instead of submitting them", () => {
  const { context, nodes } = prescriptionContext();
  nodes.get("#pending-prn").checked = true;
  nodes.get("#pending-long-term").checked = true;

  const payload = context.prescriptionPayloadFromForm();

  assert.equal(payload.frequency_per_day, null);
  assert.equal(payload.schedule_times.length, 0);
  assert.equal(payload.prescription_days, null);
  assert.equal(payload.prn_max_per_day, 2);
});

test("medication cards separate DUR review, editing, and regimen completion semantics", () => {
  const app = source("app.js");

  assert.match(app, /data-dur-alert[\s\S]{0,300}openMedicationSafety/);
  assert.match(app, />복용 종료<\/button>/);
  assert.doesNotMatch(app, /data-stop="\$\{med\.id\}"[^>]*>삭제<\/button>/);
  assert.match(app, /사용 완료/);
  assert.match(app, /data-instance-cancel[\s\S]{0,120}>되돌리기<\/button>/);
});

test("main content is not one giant live region", () => {
  const index = source("index.html");
  assert.doesNotMatch(index, /id="app-main"[^>]*aria-live/);
});

test("bottom-sheet close controls meet the 44px touch target", () => {
  const styles = source("styles.css");
  const block = styles.match(/(?:^|\n)\.icon-button\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(block, /width:\s*44px/);
  assert.match(block, /height:\s*44px/);
});
