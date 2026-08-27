const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function timelineContext() {
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../ui/dist/timeline.js"), "utf8"),
    context,
  );
  return context;
}

test("renders active course day and remaining days after today", () => {
  const context = timelineContext();

  const html = context.medicationCourseHtml({
    status: "active", total_days: 5, current_day: 2,
    remaining_days: 3, progress_percent: 40,
  });

  assert.match(html, /전체 5일/);
  assert.match(html, /2일째/);
  assert.match(html, /3일 남음/);
  assert.match(html, /<progress[^>]+value="40"[^>]+max="100"/);
  assert.match(html, /aria-label="복용 진행률 40%"/);
  assert.doesNotMatch(html, /style=/);
});

test("renders upcoming, completed, and indefinite courses explicitly", () => {
  const context = timelineContext();

  const upcoming = context.medicationCourseHtml({ status: "upcoming", total_days: 3, current_day: 0, remaining_days: 3, progress_percent: 0 });
  const completed = context.medicationCourseHtml({ status: "completed", total_days: 3, current_day: 3, remaining_days: 0, progress_percent: 100 });
  assert.match(upcoming, /시작 전/);
  assert.match(upcoming, /value="0"/);
  assert.match(completed, /복용기간 종료/);
  assert.match(completed, /value="100"/);
  assert.equal(context.medicationCourseHtml(null), "");
});
