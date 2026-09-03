"use strict";

const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const staticDir = path.join(__dirname, "../../ui/dist");
const source = (name) => fs.readFileSync(path.join(staticDir, name), "utf8");

test("Android profile screen exposes medication reminder controls without adding a new nav destination", () => {
  const index = source("index.html");
  const reminders = source("reminders.js");

  assert.match(index, /id="reminder-settings"/);
  assert.match(index, /\/static\/reminders\.js\?v=20260903reminders2/);
  assert.doesNotMatch(index, /data-nav="reminders"/);
  assert.match(reminders, /MedicineReminderNative/);
  assert.match(reminders, /모든 프로필/);
});

test("saving a fixed medication schedule offers reminders through the native bridge", () => {
  const prescription = source("prescription.js");

  const calls = prescription.match(/offerRemindersAfterScheduleSave\(draft\.schedule_times\)/g) || [];
  assert.match(prescription, /MedicineReminderUi\?\.offerAfterScheduledMedicationSave/);
  assert.equal(calls.length, 2);
});
