"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function appContext(storage = new Map()) {
  const home = {
    innerHTML: "",
    querySelectorAll() { return []; },
  };
  const medications = { innerHTML: "", querySelectorAll() { return []; } };
  const history = { innerHTML: "", querySelectorAll() { return []; } };
  const toast = {
    textContent: "",
    classList: { add() {}, remove() {} },
  };
  const document = {
    querySelector(selector) {
      if (selector === "#home-content") return home;
      if (selector === "#medications-list") return medications;
      if (selector === "#dose-history") return history;
      if (selector === "#toast") return toast;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const context = {
    document,
    window: { MedicineLocalApi: null, location: { search: "" } },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    profileMeta: () => "만 36세 · 남성",
    MedicineOcr: { getReview: () => null, cancel() {}, toggle() {}, init() {}, renderState() {} },
    bindPeopleEvents() {},
    renderProfileShortcut() {},
    renderPeople() {},
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
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../medicine_app/static/dose-actions.js"), "utf8"),
    context,
  );
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

test("historical dose correction refreshes history without replacing today's plan", () => {
  const { context } = appContext();
  const result = vm.runInContext(`
    state.dashboardDate = "2026-08-20";
    state.dashboard = {
      medications: [],
      recent_logs: [{ id: "old-log", dose_instance_id: "old-dose" }],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "today-dose", status: "planned", completed_at: null }],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    reconcileDoseMutation({
      id: "old-dose",
      status: "planned",
      completed_at: null,
      scheduled_date: "2026-08-19",
      recent_logs: [],
    });
    ({
      dashboardDate: state.dashboardDate,
      planDate: state.dashboard.daily_plan.date,
      todayStatus: state.dashboard.daily_plan.doses[0].status,
      logCount: state.dashboard.recent_logs.length,
    });
  `, context);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result)),
    { dashboardDate: "2026-08-20", planDate: "2026-08-20", todayStatus: "planned", logCount: 0 },
  );
});

test("current dose reconciliation updates the local plan summary and recent history", () => {
  const { context } = appContext();
  const result = vm.runInContext(`
    state.dashboard = {
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [
          { id: "dose-1", status: "planned", completed_at: null },
          { id: "dose-2", status: "skipped", completed_at: "2026-08-20T09:00:00+09:00" },
        ],
        summary: { planned: 1, taken: 0, skipped: 1 },
      },
    };
    reconcileDoseMutation({
      id: "dose-1",
      status: "taken",
      completed_at: "2026-08-20T08:05:00+09:00",
      recent_logs: [{ id: "log-1", dose_instance_id: "dose-1", status: "taken" }],
    });
    ({
      status: state.dashboard.daily_plan.doses[0].status,
      completedAt: state.dashboard.daily_plan.doses[0].completed_at,
      summary: state.dashboard.daily_plan.summary,
      logCount: state.dashboard.recent_logs.length,
    });
  `, context);

  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    status: "taken",
    completedAt: "2026-08-20T08:05:00+09:00",
    summary: { planned: 0, taken: 1, skipped: 1 },
    logCount: 1,
  });
});

test("dose reconciliation never clears an unrelated stale medication snapshot", () => {
  const { context } = appContext();
  const result = vm.runInContext(`
    state.dashboardStale = true;
    state.dashboard = {
      medications: [{ id: "med-1", product_name: "변경된 약" }],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null }],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    reconcileDoseMutation({
      id: "dose-1",
      status: "taken",
      completed_at: "2026-08-20T08:05:00+09:00",
      recent_logs: [{ id: "log-1", dose_instance_id: "dose-1", status: "taken" }],
    });
    ({
      dashboardStale: state.dashboardStale,
      status: state.dashboard.daily_plan.doses[0].status,
      logCount: state.dashboard.recent_logs.length,
    });
  `, context);

  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    dashboardStale: true,
    status: "taken",
    logCount: 1,
  });
});

test("dose state intents keep the running write and coalesce queued changes to the latest desired state", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboardDate = "2026-08-20";
    state.dashboard = {
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null, product_name: "약A" }],
        prn_medications: [],
        unscheduled_medications: [],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-1", "taken");
    cancelDoseInstance("dose-1");
    completeDoseInstance("dose-1", "skipped");
  `, context);

  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "POST");
  assert.equal(JSON.parse(requests[0].options.body).status, "taken");

  requests[0].resolve({
    id: "dose-1",
    status: "taken",
    completed_at: "2026-08-20T08:05:00+09:00",
    recent_logs: [{ id: "log-taken", dose_instance_id: "dose-1", status: "taken" }],
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 2);
  assert.equal(requests[1].options.method, "POST");
  assert.equal(JSON.parse(requests[1].options.body).status, "skipped");
  assert.equal(vm.runInContext(`state.dashboard.daily_plan.doses[0].status`, context), "skipped");

  requests[1].resolve({
    id: "dose-1",
    status: "skipped",
    completed_at: "2026-08-20T08:06:00+09:00",
    recent_logs: [{ id: "log-skipped", dose_instance_id: "dose-1", status: "skipped" }],
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(vm.runInContext(`state.dashboard.daily_plan.doses[0].status`, context), "skipped");
  assert.equal(vm.runInContext(`state.dashboard.recent_logs[0].status`, context), "skipped");
  assert.equal(vm.runInContext(`state.doseMutations.size`, context), 0);
});

test("scheduled dose desired state survives WebView recreation", async () => {
  const storage = new Map();
  const first = appContext(storage);
  const firstRequests = [];
  first.context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => firstRequests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null }],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-1", "taken");
    completeDoseInstance("dose-1", "skipped");
  `, first.context);

  assert.equal(firstRequests.length, 1);
  assert.equal(JSON.parse(firstRequests[0].options.body).status, "taken");
  const stored = JSON.parse(storage.get("medicine.doseIntents"));
  assert.deepEqual(stored["dose-1"], { personId: "p1", desiredStatus: "skipped" });

  const second = appContext(storage);
  const recoveredRequests = [];
  second.context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => recoveredRequests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      medications: [],
      recent_logs: [{ id: "log-taken", dose_instance_id: "dose-1", status: "taken" }],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "taken", completed_at: "2026-08-20T08:05:00+09:00" }],
        summary: { planned: 0, taken: 1, skipped: 0 },
      },
    };
    recoverPersistedDoseIntents("p1");
  `, second.context);

  assert.equal(recoveredRequests.length, 1);
  assert.equal(recoveredRequests[0].path, "/api/dose-instances/dose-1");
  assert.equal(JSON.parse(recoveredRequests[0].options.body).status, "skipped");
  recoveredRequests[0].resolve({
    id: "dose-1",
    status: "skipped",
    completed_at: "2026-08-20T08:06:00+09:00",
    recent_logs: [{ id: "log-skipped", dose_instance_id: "dose-1", status: "skipped" }],
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(storage.has("medicine.doseIntents"), false);
  assert.equal(vm.runInContext(`state.dashboard.daily_plan.doses[0].status`, second.context), "skipped");
});

test("definitive scheduled dose failure clears durable intent after profile switch", async () => {
  const storage = new Map();
  const { context } = appContext(storage);
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [
      { id: "A", name: "A", sex: "male", age: 36 },
      { id: "B", name: "B", sex: "male", age: 36 },
    ];
    state.currentPersonId = "A";
    state.dashboard = {
      person: { id: "A" },
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-A", status: "planned", completed_at: null }],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-A", "taken");
  `, context);

  assert.equal(requests.length, 1);
  assert.ok(storage.has("medicine.doseIntents"));
  vm.runInContext(`
    state.currentPersonId = "B";
    state.dashboard = {
      person: { id: "B" },
      medications: [],
      recent_logs: [{ id: "b-log", status: "taken" }],
      daily_plan: { date: "2026-08-20", doses: [], summary: { planned: 0, taken: 0, skipped: 0 } },
    };
  `, context);
  const rejected = new Error("dose no longer exists");
  rejected.status = 404;
  requests[0].reject(rejected);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(storage.has("medicine.doseIntents"), false);
  assert.equal(vm.runInContext(`state.currentPersonId`, context), "B");
  assert.equal(vm.runInContext(`state.dashboard.recent_logs[0].id`, context), "b-log");
  assert.equal(vm.runInContext(`state.doseMutations.size`, context), 0);
});

test("ambiguous dose failure refreshes authoritative state before converging to a queued older state", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboardDate = "2026-08-20";
    state.dashboard = {
      person: { id: "p1" },
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null, product_name: "약A" }],
        prn_medications: [],
        unscheduled_medications: [],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-1", "taken");
    cancelDoseInstance("dose-1");
  `, context);

  assert.equal(requests.length, 1);
  const ambiguous = new Error("response lost after commit");
  ambiguous.status = 500;
  requests[0].reject(ambiguous);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 2);
  assert.equal(requests[1].path, "/api/people/p1/dashboard");
  requests[1].resolve({
    person: { id: "p1" },
    medications: [],
    recent_logs: [{ id: "log-taken", dose_instance_id: "dose-1", status: "taken" }],
    daily_plan: {
      date: "2026-08-20",
      doses: [{ id: "dose-1", status: "taken", completed_at: "2026-08-20T08:05:00+09:00" }],
      prn_medications: [],
      unscheduled_medications: [],
      summary: { planned: 0, taken: 1, skipped: 0 },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 3);
  assert.equal(requests[2].path, "/api/dose-instances/dose-1/completion");
  assert.equal(requests[2].options.method, "DELETE");
  requests[2].resolve({
    id: "dose-1",
    status: "planned",
    completed_at: null,
    recent_logs: [],
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(vm.runInContext(`state.dashboard.daily_plan.doses[0].status`, context), "planned");
  assert.equal(vm.runInContext(`state.doseMutations.size`, context), 0);
});

test("dose intents stay queued while ambiguous failure reconciliation is in flight", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      person: { id: "p1" },
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null }],
        prn_medications: [],
        unscheduled_medications: [],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-1", "taken");
  `, context);

  const ambiguous = new Error("response lost after commit");
  ambiguous.status = 500;
  requests[0].reject(ambiguous);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.equal(requests[1].path, "/api/people/p1/dashboard");

  vm.runInContext(`completeDoseInstance("dose-1", "skipped")`, context);
  assert.equal(requests.length, 2);

  requests[1].resolve({
    person: { id: "p1" },
    medications: [],
    recent_logs: [{ id: "log-taken", dose_instance_id: "dose-1", status: "taken" }],
    daily_plan: {
      date: "2026-08-20",
      doses: [{ id: "dose-1", status: "taken", completed_at: "2026-08-20T08:05:00+09:00" }],
      prn_medications: [],
      unscheduled_medications: [],
      summary: { planned: 0, taken: 1, skipped: 0 },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 3);
  assert.equal(requests[2].path, "/api/dose-instances/dose-1");
  assert.equal(JSON.parse(requests[2].options.body).status, "skipped");
});

test("persistent ambiguous dose failures stop after one authoritative compensation", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      person: { id: "p1" },
      medications: [],
      recent_logs: [],
      daily_plan: {
        date: "2026-08-20",
        doses: [{ id: "dose-1", status: "planned", completed_at: null }],
        prn_medications: [],
        unscheduled_medications: [],
        summary: { planned: 1, taken: 0, skipped: 0 },
      },
    };
    completeDoseInstance("dose-1", "taken");
  `, context);

  const firstFailure = new Error("first ambiguous failure");
  firstFailure.status = 500;
  requests[0].reject(firstFailure);
  await new Promise((resolve) => setImmediate(resolve));
  requests[1].resolve({
    person: { id: "p1" },
    medications: [],
    recent_logs: [],
    daily_plan: {
      date: "2026-08-20",
      doses: [{ id: "dose-1", status: "planned", completed_at: null }],
      prn_medications: [],
      unscheduled_medications: [],
      summary: { planned: 1, taken: 0, skipped: 0 },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 3);
  const secondFailure = new Error("second ambiguous failure");
  secondFailure.status = 500;
  requests[2].reject(secondFailure);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests.length, 4);
  requests[3].resolve({
    person: { id: "p1" },
    medications: [],
    recent_logs: [],
    daily_plan: {
      date: "2026-08-20",
      doses: [{ id: "dose-1", status: "planned", completed_at: null }],
      prn_medications: [],
      unscheduled_medications: [],
      summary: { planned: 1, taken: 0, skipped: 0 },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 4);
  assert.equal(vm.runInContext(`state.dashboard.daily_plan.doses[0].status`, context), "planned");
  assert.equal(vm.runInContext(`state.doseMutations.size`, context), 0);
});

test("PRN undo treats authoritative deletion as terminal and never retries the deleted instance", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path, options = {}) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.people = [{ id: "p1", name: "검토", sex: "male", age: 36 }];
    state.currentPersonId = "p1";
    state.dashboard = {
      medications: [],
      recent_logs: [{ id: "prn-log", dose_instance_id: "prn-1", status: "taken" }],
      daily_plan: {
        date: "2026-08-20",
        doses: [],
        prn_medications: [],
        unscheduled_medications: [],
        summary: { planned: 0, taken: 0, skipped: 0 },
      },
    };
    cancelDoseInstance("prn-1");
  `, context);

  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "DELETE");

  requests[0].resolve({
    id: "prn-1",
    status: "canceled",
    deleted: true,
    completed_at: null,
    recent_logs: [],
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 1);
  assert.equal(vm.runInContext(`state.dashboard.recent_logs.length`, context), 0);
  assert.equal(vm.runInContext(`state.doseMutations.size`, context), 0);
});

test("dashboard refresh is single-flight and collapses concurrent refreshes to one final reload", async () => {
  const { context } = appContext();
  const requests = [];
  context.window.MedicineLocalApi = {
    request(path) {
      return new Promise((resolve, reject) => requests.push({ path, resolve, reject }));
    },
  };
  vm.runInContext(`
    state.currentPersonId = "p1";
    state.dashboardStale = true;
    state.dashboard = {
      person: { id: "p1" },
      medications: [{ id: "visible-before-refresh" }],
      recent_logs: [],
      daily_plan: { date: "2026-08-20", doses: [], summary: {} },
    };
  `, context);

  const first = vm.runInContext(`loadDashboard()`, context);
  const second = vm.runInContext(`loadDashboard()`, context);

  assert.equal(requests.length, 1);
  requests[0].resolve({
    person: { id: "p1" },
    medications: [],
    recent_logs: [],
    daily_plan: { date: "2026-08-20", doses: [], summary: {} },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests.length, 2);
  assert.equal(vm.runInContext(`state.dashboardStale`, context), true);
  assert.equal(
    vm.runInContext(`state.dashboard.medications[0].id`, context),
    "visible-before-refresh",
  );
  requests[1].resolve({
    person: { id: "p1" },
    medications: [{ id: "latest" }],
    recent_logs: [],
    daily_plan: { date: "2026-08-20", doses: [], summary: {} },
  });
  await Promise.all([first, second]);

  assert.equal(vm.runInContext(`state.dashboard.medications[0].id`, context), "latest");
  assert.equal(vm.runInContext(`state.dashboardStale`, context), false);
  assert.equal(vm.runInContext(`state.dashboardLoads.size`, context), 0);
});
