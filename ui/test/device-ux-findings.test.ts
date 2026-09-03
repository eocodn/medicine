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

function medicationAddContext() {
  const nodes = new Map();
  const add = (selector, initial = {}) => nodes.set(selector, {
    value: "", checked: false, disabled: false, innerHTML: "", textContent: "",
    addEventListener() {}, setAttribute() {}, removeAttribute() {}, ...initial,
  });
  add("#pending-dose-amount", { value: "1" });
  add("#pending-dose-unit", { value: "정" });
  add("#pending-frequency", { value: "1" });
  add("#pending-days", { value: "7" });
  add("#pending-times", { value: "08:00" });
  add("#pending-meal", { value: "after_meal" });
  add("#pending-route", { value: "oral" });
  add("#pending-start-date", { value: "2026-08-19" });
  add("#pending-prn", { checked: false });
  add("#pending-long-term", { checked: false });
  add("#pending-prn-max", { value: "" });
  add("#quantitative-warning");
  add("#confirm-add-med");

  const events = [];
  const created = {
    id: "med-2",
    product_name: "두번째약",
    ingredient_name: "성분B",
    schedules: [{ time_of_day: "08:00", dose_text: null }],
    active: true,
    source: "catalog",
    revision: 1,
  };
  const context = {
    document: {},
    state: {
      currentPersonId: "person-1",
      pendingProduct: { product_ref: "product-2" },
      pendingRequestId: "request-2",
      warningToken: null,
      reviewedDraftKey: null,
      dashboardSession: {
        ownerPersonId: "person-1", date: "2026-08-19", phase: "ready", generation: 1, reason: null,
        data: { medications: [{ id: "med-1", product_name: "첫번째약" }] },
      },
    },
    $: (selector) => nodes.get(selector) || null,
    $$: () => [],
    api: async (url, options) => {
      assert.match(url, /\/medications$/);
      assert.equal(options.method, "POST");
      events.push("created");
      return created;
    },
    loadDashboard: async () => {
      events.push("refresh");
      throw new Error("refresh failed");
    },
    renderAll: () => events.push("render"),
    showScreen: (name) => events.push(`screen:${name}`),
    completeOcrProductRowAndContinue: () => false,
    closeSheets: () => events.push("close"),
    closeSheetsAfterMutation: () => events.push("close"),
    dashboardData: () => context.state.dashboardSession.data,
    markDashboardStale: () => { context.state.dashboardSession.phase = "stale"; events.push("stale"); },
    toast: (message) => events.push(`toast:${message}`),
    escapeHtml: (value) => String(value ?? ""),
    assessmentDetailsHtml: () => "",
    console,
    crypto,
  };
  vm.createContext(context);
  vm.runInContext(source("prescription.js"), context);
  context.state.reviewedDraftKey = JSON.stringify(context.prescriptionPayloadFromForm());
  return { context, events };
}

test("entering medication search does not focus the input or open the keyboard", () => {
  const app = source("app.js");
  assert.doesNotMatch(app, /name === "search"[^\n]*\.focus\(/);
});

test("birth date uses direct year month day selectors instead of a calendar-only input", () => {
  const index = source("index.html");
  const people = source("people.js");
  assert.doesNotMatch(index, /name="birth_date"\s+type="date"/);
  assert.match(index, /name="birth_year"\s+type="number"[^>]*inputmode="numeric"/);
  assert.match(index, /name="birth_month"/);
  assert.match(index, /name="birth_day"/);
  assert.match(index, /name="birth_date"\s+type="hidden"/);
  assert.match(people, /syncBirthDateFields/);
  assert.match(people, /setBirthDateFields/);
});

test("a committed medication remains in local state when only the dashboard refresh fails", async () => {
  const { context, events } = medicationAddContext();

  await context.confirmAddMedication();

  assert.deepEqual(
    Array.from(context.state.dashboardSession.data.medications, (item) => item.id),
    ["med-1", "med-2"],
  );
  assert.ok(events.includes("screen:meds"));
  assert.ok(events.some((item) => item.startsWith("toast:약은 저장")));
});

test("dose action handlers provide immediate feedback and coalesce queued state changes", () => {
  const app = source("app.js");
  const doseActions = source("dose-actions.js");
  assert.match(app, /completeDoseInstance\([^,]+,[^,]+,\s*button\)/);
  assert.match(app, /cancelDoseInstance\([^,]+,\s*button\)/);
  assert.match(doseActions, /button\.disabled\s*=\s*true/);
  assert.match(doseActions, /button\.setAttribute\("aria-busy",\s*"true"\)/);
  assert.match(doseActions, /처리 중/);
  assert.match(doseActions, /state\.doseMutations\.get\(instanceId\)/);
  assert.match(doseActions, /entry\.desiredStatus\s*!==\s*updated\.status/);
  assert.match(doseActions, /drainDoseDesiredState\(instanceId,\s*entry\)/);
  assert.match(doseActions, /reconcileDoseMutation\(updated\)/);
});

test("profile deletion uses an in-app destructive confirmation sheet", () => {
  const index = source("index.html");
  const people = source("people.js");
  assert.match(index, /id="delete-person-sheet"/);
  assert.match(index, /id="confirm-delete-person"/);
  assert.doesNotMatch(people, /\bconfirm\(/);
  assert.match(people, /openSheet\("#delete-person-sheet"\)/);
});