const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

function timelineContext() {
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../medicine_app/static/timeline.js"), "utf8"),
    context,
  );
  return context;
}

test("renders active medication course day and inclusive remaining days", () => {
  const context = timelineContext();

  const html = context.medicationCourseHtml({
    status: "active", total_days: 5, current_day: 2,
    remaining_days: 4, progress_percent: 40,
  });

  assert.match(html, /전체 5일/);
  assert.match(html, /2일째/);
  assert.match(html, /4일 남음/);
  assert.match(html, /width:40%/);
});

test("renders upcoming, completed, and indefinite courses explicitly", () => {
  const context = timelineContext();

  assert.match(context.medicationCourseHtml({ status: "upcoming", total_days: 3, current_day: 0, remaining_days: 3, progress_percent: 0 }), /시작 전/);
  assert.match(context.medicationCourseHtml({ status: "completed", total_days: 3, current_day: 3, remaining_days: 0, progress_percent: 100 }), /복용기간 종료/);
  assert.equal(context.medicationCourseHtml(null), "");
});
