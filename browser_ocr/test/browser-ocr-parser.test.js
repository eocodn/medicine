"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parsePrescriptionHints } = require("../../medicine_app/static/browser-ocr-parser.js");

test("normalizes Korean prescription into structured hints", () => {
  const hints = parsePrescriptionHints("약명: 타이레놀정\n1정 1일 2회 7일\n오전 8시 오후 2시");
  assert.deepEqual(hints.product_queries, ["타이레놀정"]);
  assert.equal(hints.dose_quantity, 1);
  assert.equal(hints.dose_unit, "정");
  assert.equal(hints.frequency_per_day, 2);
  assert.equal(hints.duration_days, 7);
  assert.deepEqual(hints.times, ["08:00", "14:00"]);
});

test("normalizes English prescription and afternoon time", () => {
  const hints = parsePrescriptionHints("Product: amoxicillin\n1 capsule 2 times/day for 7 days\nPM 2:30");
  assert.deepEqual(hints.product_queries, ["amoxicillin"]);
  assert.equal(hints.dose_unit, "캡슐");
  assert.deepEqual(hints.times, ["14:30"]);
});

test("makes ambiguity and unsupported instructions explicit", () => {
  const hints = parsePrescriptionHints("약명: 타이레놀정\n제품명: 이부프로펜정\n필요시 주사");
  assert.deepEqual(hints.product_queries, ["타이레놀정", "이부프로펜정"]);
  assert.ok(hints.ambiguity_codes.includes("AMBIGUOUS_PRODUCT"));
  assert.ok(hints.unsupported_codes.includes("UNSUPPORTED_AS_NEEDED"));
  assert.ok(hints.unsupported_codes.includes("UNSUPPORTED_ROUTE"));
  assert.equal(JSON.stringify(hints).includes("필요시 주사"), false);
});

test("does not invent a product from unrelated text", () => {
  const hints = parsePrescriptionHints("환자용 안내문\n혈압을 확인하세요\n1일 3회");
  assert.deepEqual(hints.product_queries, []);
  assert.ok(hints.ambiguity_codes.includes("MISSING_PRODUCT"));
  assert.equal(hints.frequency_per_day, 3);
});

test("prefers labeled medication-bag values over numeric row headings", () => {
  const hints = parsePrescriptionHints([
    "약명: 타이레놀정",
    "1회 복용량: 1정",
    "1일 복용횟수: 2회",
    "총 복용일수: 7일",
    "복용시간: 오전 8시 오후 8시",
  ].join("\n"));

  assert.equal(hints.dose_quantity, 1);
  assert.equal(hints.frequency_per_day, 2);
  assert.equal(hints.duration_days, 7);
});
