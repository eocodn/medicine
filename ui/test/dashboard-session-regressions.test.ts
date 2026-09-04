"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const TODAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());

function harness(storage = new Map()) {
  const nodes = new Map();
  const makeNode = () => ({
    innerHTML: "",
    textContent: "",
    value: "",
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {},
    removeAttribute() {},
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
    contains() { return false; },
    focus() {},
  });
  for (const selector of [
    "#home-content", "#medications-list", "#dose-history", "#toast",
    "#profile-shortcut-avatar", "#profile-shortcut-name", "#profile-shortcut",
    "#people-list", "#page-title",
  ]) nodes.set(selector, makeNode());
  const document = {
    visibilityState: "visible",
    activeElement: null,
    querySelector(selector) { return nodes.get(selector) || null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: {
      MedicineLocalApi: null,
      MedicineReminderUi: { refresh() {} },
      MedicineOcrIntake: { reset() {} },
      location: { search: "" },
      addEventListener() {},
    },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    friendlyErrorMessage: (value) => String(value),
    MedicineOcr: { getReview: () => null, cancel() {}, toggle() {}, init() {}, renderState() {} },
    openPersonForm() {},
    openMedicationSafety() {},
    openMedicationEdit() {},
    recordPrnIntake() {},
    medicationCourseHtml() { return ""; },
    completeDoseInstance() {},
    cancelDoseInstance() {},
    bindPeopleEvents() {},
    showScreen() {},
    focusPageTitle() {},
    console,
    Intl,
    Date,
    URLSearchParams,
    setTimeout,
    clearTimeout,
    setInterval() { return 0; },
    crypto,
    fetch: async () => { throw new Error("unexpected fetch"); },
  };
  vm.createContext(context);
  for (const name of ["app-state.js", "app.js", "dashboard-runtime.js", "people.js", "mutation-invariants.js", "dose-actions.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../../ui/dist", name), "utf8"), context);
  }
  return { context, nodes };
}

function dashboard(personId, medicationId = "med-1", date = TODAY) {
  return {
    person: { id: personId, name: personId },
    medications: [{ id: medicationId, product_name: medicationId, revision: 1 }],
    recent_logs: [],
    daily_plan: { date, doses: [], prn_medications: [], unscheduled_medications: [], summary: {} },
  };
}

test("people stay visible when cold-start dashboard loading fails", async () => {
  const { context, nodes } = harness(new Map([["medicine.currentPersonId", "p1"]]));
  context.window.MedicineLocalApi = {
    async request(path) {
      if (path === "/api/people") return [{ id: "p1", name: "나", age: 36, sex: "male" }];
      if (path === "/api/people/p1/dashboard") throw new Error("dashboard unavailable");
      throw new Error(`unexpected ${path}`);
    },
  };

  await vm.runInContext("loadPeople()", context);
  await assert.rejects(vm.runInContext("loadDashboard()", context), /dashboard unavailable/);
  vm.runInContext("renderAll()", context);

  assert.equal(vm.runInContext("state.people.length", context), 1);
  assert.equal(vm.runInContext("state.currentPersonId", context), "p1");
  assert.match(nodes.get("#profile-shortcut-name").textContent, /나/);
  assert.match(nodes.get("#home-content").innerHTML, /나/);
  assert.doesNotMatch(nodes.get("#home-content").innerHTML, /누구의 약을/);
});

test("foreground recovery reloads people after the cold-start people request fails", async () => {
  const { context, nodes } = harness(new Map([["medicine.currentPersonId", "p1"]]));
  let peopleRequests = 0;
  context.window.MedicineLocalApi = {
    async request(path) {
      if (path === "/api/people") {
        peopleRequests += 1;
        if (peopleRequests === 1) throw new Error("personal database temporarily unavailable");
        return [{ id: "p1", name: "나", age: 36, sex: "male" }];
      }
      if (path === "/api/people/p1/dashboard") return dashboard("p1", "med-1");
      throw new Error(`unexpected ${path}`);
    },
  };

  await assert.rejects(vm.runInContext("loadPeople()", context), /temporarily unavailable/);
  assert.equal(vm.runInContext("state.people.length", context), 0);

  await vm.runInContext("refreshForForeground()", context);

  assert.equal(peopleRequests, 2);
  assert.equal(vm.runInContext("state.currentPersonId", context), "p1");
  assert.match(nodes.get("#profile-shortcut-name").textContent, /나/);
  assert.equal(vm.runInContext("state.dashboardSession.phase", context), "ready");
});

test("failed profile switch never leaves the previous dashboard owned by the new profile", async () => {
  const { context } = harness(new Map([["medicine.currentPersonId", "A"]]));
  context.window.MedicineLocalApi = {
    async request(path) {
      if (path === "/api/people/B/dashboard") throw new Error("B dashboard unavailable");
      throw new Error(`unexpected ${path}`);
    },
  };
  vm.runInContext(`
    state.people = [
      { id: "A", name: "A", age: 36, sex: "male" },
      { id: "B", name: "B", age: 36, sex: "male" },
    ];
    state.currentPersonId = "A";
    state.dashboardSession = {
      ownerPersonId: "A", date: "${TODAY}", phase: "ready",
      data: ${JSON.stringify(dashboard("A", "med-A"))}, generation: 1, reason: null,
    };
  `, context);

  await vm.runInContext("selectPerson('B')", context);

  const result = vm.runInContext(`({
    active: state.currentPersonId,
    sessionOwner: state.dashboardSession.ownerPersonId,
    dashboardOwner: state.dashboardSession.data?.person?.id || null,
    medication: state.dashboardSession.data?.medications?.[0]?.id || null,
    phase: state.dashboardSession.phase,
  })`, context);
  assert.equal(result.active, "B");
  assert.equal(result.sessionOwner, "B");
  assert.equal(result.phase, "error");
  assert.notEqual(result.dashboardOwner, "A");
  assert.notEqual(result.medication, "med-A");
});

test("failed date rollover invalidates the previous-day actionable dashboard", async () => {
  const { context, nodes } = harness(new Map([["medicine.currentPersonId", "p1"]]));
  context.window.MedicineLocalApi = {
    async request(path) {
      if (path === "/api/people/p1/dashboard") throw new Error("rollover unavailable");
      throw new Error(`unexpected ${path}`);
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "나", age: 36, sex: "male" }];
    state.currentPersonId = "p1";
    state.dashboardSession = {
      ownerPersonId: "p1", date: "2000-01-01", phase: "ready", generation: 1, reason: null,
      data: ${JSON.stringify({
      ...dashboard("p1", "med-old", "2000-01-01"),
      daily_plan: {
        date: "2000-01-01",
        doses: [{ id: "old-dose", status: "planned", product_name: "어제약", scheduled_time: "08:00" }],
        prn_medications: [], unscheduled_medications: [], summary: { planned: 1, taken: 0, skipped: 0 },
      },
    })},
    };
    renderAll();
  `, context);
  assert.doesNotMatch(nodes.get("#home-content").innerHTML, /data-instance-taken="old-dose"/);

  await vm.runInContext("refreshForDateChange()", context);

  vm.runInContext("renderAll()", context);
  assert.doesNotMatch(nodes.get("#home-content").innerHTML, /data-instance-taken="old-dose"/);
  assert.doesNotMatch(nodes.get("#medications-list").innerHTML, /med-old/);
});


test("late dashboard response from a previous profile can never replace the active profile", async () => {
  const { context } = harness();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path) {
      return new Promise((resolve, reject) => requests.push({ path, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [
      { id: "A", name: "A", age: 36, sex: "male" },
      { id: "B", name: "B", age: 36, sex: "male" },
    ];
    selectCurrentPerson("A");
  `, context);

  const loadA = vm.runInContext("loadDashboard()", context);
  vm.runInContext('selectCurrentPerson("B")', context);
  const loadB = vm.runInContext("loadDashboard()", context);
  assert.equal(requests.length, 2);

  requests[1].resolve(dashboard("B", "med-B"));
  await loadB;
  requests[0].resolve(dashboard("A", "med-A"));
  await loadA;

  assert.equal(vm.runInContext("state.currentPersonId", context), "B");
  assert.equal(vm.runInContext("state.dashboardSession.ownerPersonId", context), "B");
  assert.equal(vm.runInContext("state.dashboardSession.data.person.id", context), "B");
  assert.equal(vm.runInContext("state.dashboardSession.data.medications[0].id", context), "med-B");
});

test("dashboard invalidated during an in-flight load performs a trailing authoritative reload", async () => {
  const { context } = harness();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path) {
      return new Promise((resolve, reject) => requests.push({ path, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "나", age: 36, sex: "male" }];
    selectCurrentPerson("p1");
  `, context);

  const first = vm.runInContext("loadDashboard()", context);
  vm.runInContext('markDashboardStale("committed_mutation")', context);
  const trailing = vm.runInContext("loadDashboard()", context);
  assert.equal(requests.length, 1);

  requests[0].resolve(dashboard("p1", "obsolete"));
  await first;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.notEqual(vm.runInContext("state.dashboardSession.data?.medications?.[0]?.id || null", context), "obsolete");

  requests[1].resolve(dashboard("p1", "latest"));
  await trailing;
  assert.equal(vm.runInContext("state.dashboardSession.phase", context), "ready");
  assert.equal(vm.runInContext("state.dashboardSession.data.medications[0].id", context), "latest");
});
