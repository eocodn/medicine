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
  assert.match(index, /data-nav="people"[^>]*>[\s\S]*?<small>프로필<\/small>/);
  assert.doesNotMatch(index, /data-nav="people"[^>]*>[\s\S]*?<small>가족<\/small>/);
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
  const styles = source("styles.css");

  assert.match(app, /data-dur-alert[\s\S]{0,300}openMedicationSafety/);
  assert.match(app, />DUR 주의<\/button>/);
  assert.match(app, />DUR 확인 필요<\/button>/);
  assert.match(app, /class="regimen-summary"/);
  assert.match(app, />복용 종료<\/button>/);
  assert.doesNotMatch(app, /data-stop="\$\{med\.id\}"[^>]*>삭제<\/button>/);
  assert.match(styles, /\.med-badges\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*flex:\s*0\s+0\s+auto;/);
});

test("home dose actions prioritize normal completion and compact completed states", () => {
  const app = source("app.js");
  const styles = source("styles.css");

  assert.match(app, /class="dose-primary-action"[^>]*data-instance-taken[^>]*>✓ 사용했어요<\/button>/);
  assert.match(app, /class="dose-skip-action"[^>]*data-instance-skipped[^>]*>건너뛰기<\/button>/);
  assert.match(app, /class="dose-status taken"[^>]*>✓ 복용 완료<\/span>/);
  assert.match(app, /class="dose-status skipped"[^>]*>– 건너뜀<\/span>/);
  assert.match(app, /class="dose-cancel-action"[^>]*data-instance-cancel[^>]*>취소<\/button>/);
  assert.match(styles, /\.dose-actions\.planned[^{]*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto;/);
  assert.match(styles, /\.dose-primary-action\s*\{[^}]*background:\s*var\(--brand\);[^}]*color:\s*white;/);
  assert.match(styles, /\.dose-skip-action, \.dose-cancel-action\s*\{[^}]*background:\s*transparent;/);
  assert.doesNotMatch(styles, /\.schedule-item\.done\s*\{[^}]*opacity:/);
});

test("schedule entry uses explicit time controls and immediate count guidance", () => {
  const prescription = source("prescription.js");

  assert.match(prescription, /type="hidden" id="pending-times"/);
  assert.match(prescription, /type="time"[^>]*data-schedule-time/);
  assert.match(prescription, /data-add-schedule-time/);
  assert.match(prescription, /id="schedule-time-status"[^>]*role="status"/);
  assert.match(prescription, /function scheduleCountGuidance/);
});

test("schedule count guidance identifies mismatch and duplicate times before submit", () => {
  const { context } = prescriptionContext();

  assert.equal(context.scheduleCountGuidance("3", []), "복용 시간을 추가하지 않으면 하루 횟수만 저장돼요.");
  assert.equal(context.scheduleCountGuidance("3", ["08:00", "13:00"]), "하루 3회인데 복용 시간은 2개예요.");
  assert.equal(context.scheduleCountGuidance("2", ["08:00", "08:00"]), "같은 복용 시간이 두 번 있어요.");
  assert.equal(context.scheduleCountGuidance("2", ["08:00", "20:00"]), "복용 시간 2개가 하루 횟수와 맞아요.");
});

test("native schedule controls canonicalize backend-accepted single-digit times", () => {
  const { context } = prescriptionContext();

  assert.equal(context.normalizeScheduleTimeForInput("8:00"), "08:00");
  assert.equal(context.normalizeScheduleTimeForInput("8:5"), "08:05");
  assert.equal(context.normalizeScheduleTimeForInput("23:59"), "23:59");
  assert.equal(context.normalizeScheduleTimeForInput("24:00"), "24:00");
});

test("search exposes only normal permit products in the shared UI", () => {
  const index = source("index.html");
  const app = source("app.js");

  assert.doesNotMatch(index, /include-inactive/);
  assert.doesNotMatch(index, /취소·취하·만료 품목도 검색/);
  assert.doesNotMatch(app, /include_inactive/);
  assert.match(app, /search-hero[\s\S]{0,300}has-query/);
});

test("existing medication permit changes render as a non-DUR warning", () => {
  const app = source("app.js");
  const prescription = source("prescription.js");

  assert.match(app, /permit-change-badge/);
  assert.match(app, /허가상태 변경/);
  assert.match(app, /permit_status_changed_at/);
  assert.match(prescription, /이 상태만으로 복용을 중단하지 말고/);
  assert.match(prescription, /의사·약사와 확인/);
});

test("medication names can wrap to two lines in schedules and search results", () => {
  const styles = source("styles.css");

  assert.match(styles, /\.schedule-name strong[^{]*\{[^}]*-webkit-line-clamp:\s*2/);
  assert.match(styles, /\.result-title-line strong[^{]*\{[^}]*-webkit-line-clamp:\s*2/);
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

test("new medication UX actions keep 44px mobile touch targets", () => {
  const styles = source("styles.css");
  const prescription = source("prescription.css");
  const durBlock = styles.match(/\.dur-alert-badge, \.dur-review-badge, \.split-caution-badge\s*\{([^}]*)\}/)?.[1] || "";
  const doseActionBlock = styles.match(/\.dose-primary-action, \.dose-skip-action, \.dose-cancel-action\s*\{([^}]*)\}/)?.[1] || "";
  const addBlock = prescription.match(/\.schedule-time-add\s*\{([^}]*)\}/)?.[1] || "";
  const removeBlock = prescription.match(/\.schedule-time-remove\s*\{([^}]*)\}/)?.[1] || "";

  assert.match(durBlock, /min-height:\s*44px/);
  assert.match(doseActionBlock, /min-height:\s*44px/);
  assert.match(addBlock, /min-height:\s*44px/);
  assert.match(removeBlock, /min-height:\s*44px/);
});
