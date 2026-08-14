"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parsePrescriptionHints, parsePrescriptionDocument } = require("../../medicine_app/static/browser-ocr-parser.js");

function box(text, x1, y1, x2, y2, score = 0.99) {
  return { text, score, poly: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]] };
}

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


test("structures a common prescription table into one editable medication row per drug", () => {
  const document = parsePrescriptionDocument([
    box("약품명", 10, 10, 150, 30),
    box("1회 투약량", 180, 10, 270, 30),
    box("1일 투여횟수", 300, 10, 410, 30),
    box("총투약일수", 450, 10, 550, 30),
    box("타이레놀정", 10, 50, 150, 70),
    box("1정", 190, 50, 240, 70),
    box("2회", 330, 50, 380, 70),
    box("7일", 470, 50, 520, 70),
    box("이부프로펜정", 10, 85, 160, 105),
    box("2정", 190, 85, 240, 105),
    box("3회", 330, 85, 380, 105),
    box("5일", 470, 85, 520, 105),
  ]);

  assert.equal(document.rows.length, 2);
  assert.deepEqual(document.rows[0], {
    product_query: "타이레놀정",
    dose_amount: 1,
    dose_unit: "정",
    frequency_per_day: 2,
    prescription_days: 7,
    schedule_times: [],
    meal_relation: null,
    administration_route: null,
    as_needed: false,
    association: "table_row",
  });
  assert.equal(document.rows[1].product_query, "이부프로펜정");
  assert.equal(document.rows[1].dose_amount, 2);
  assert.equal(document.rows[1].frequency_per_day, 3);
  assert.equal(document.rows[1].prescription_days, 5);
});

test("structures repeated medication-bag blocks without leaking one regimen into another", () => {
  const document = parsePrescriptionDocument([
    box("약명: 타이레놀정", 20, 10, 220, 30),
    box("1회 복용량: 1정", 20, 35, 220, 55),
    box("1일 복용횟수: 1회", 20, 60, 230, 80),
    box("총 복용일수: 7일", 20, 85, 220, 105),
    box("약명: 이부프로펜정", 20, 140, 240, 160),
    box("1회 복용량: 2정", 20, 165, 220, 185),
    box("1일 복용횟수: 3회", 20, 190, 230, 210),
    box("총 복용일수: 5일", 20, 215, 220, 235),
  ]);

  assert.equal(document.rows.length, 2);
  assert.deepEqual(
    document.rows.map((row) => [row.product_query, row.dose_amount, row.frequency_per_day, row.prescription_days]),
    [["타이레놀정", 1, 1, 7], ["이부프로펜정", 2, 3, 5]],
  );
  assert.ok(document.rows.every((row) => row.association === "labeled_block"));
});

test("applies an explicitly scoped common regimen to every drug in that group", () => {
  const document = parsePrescriptionDocument([
    box("약명: 타이레놀정", 20, 10, 220, 30),
    box("약명: 이부프로펜정", 20, 40, 240, 60),
    box("공통 복용법: 1회 1정 1일 3회 5일 식후", 20, 80, 430, 105),
  ]);

  assert.equal(document.rows.length, 2);
  assert.deepEqual(
    document.rows.map((row) => [row.dose_amount, row.dose_unit, row.frequency_per_day, row.prescription_days, row.meal_relation]),
    [[1, "정", 3, 5, "after_meal"], [1, "정", 3, 5, "after_meal"]],
  );
  assert.ok(document.rows.every((row) => row.association === "group_shared"));
});

test("does not attach a global regimen when multiple products have no structural association", () => {
  const document = parsePrescriptionDocument([
    box("약명: 타이레놀정", 20, 10, 220, 30),
    box("제품명: 이부프로펜정", 20, 50, 240, 70),
    box("1정 1일 2회 7일", 300, 150, 480, 175),
  ]);

  assert.equal(document.rows.length, 2);
  assert.ok(document.rows.every((row) => row.dose_amount === null));
  assert.ok(document.rows.every((row) => row.frequency_per_day === null));
  assert.ok(document.rows.every((row) => row.prescription_days === null));
  assert.ok(document.ambiguity_codes.includes("UNRESOLVED_REGIMEN_ASSOCIATION"));
});

test("uses table headers to interpret numeric-only dose frequency and day cells", () => {
  const document = parsePrescriptionDocument([
    box("약품명", 10, 10, 150, 30),
    box("1회 투약량", 180, 10, 270, 30),
    box("1일 투여횟수", 300, 10, 410, 30),
    box("총투약일수", 450, 10, 550, 30),
    box("타이레놀정", 10, 50, 150, 70),
    box("1", 200, 50, 220, 70),
    box("3", 340, 50, 360, 70),
    box("5", 480, 50, 500, 70),
  ]);

  assert.equal(document.rows.length, 1);
  assert.equal(document.rows[0].dose_amount, 1);
  assert.equal(document.rows[0].dose_unit, null);
  assert.equal(document.rows[0].frequency_per_day, 3);
  assert.equal(document.rows[0].prescription_days, 5);
});


test("parses decimal and simple fractional tablet doses without inflating trailing digits", () => {
  const decimal = parsePrescriptionHints("약명: 테스트정\n0.5정 1일 2회 7일");
  assert.equal(decimal.dose_quantity, 0.5);
  assert.equal(decimal.dose_unit, "정");

  const fraction = parsePrescriptionHints("약명: 테스트정\n1/2정 1일 2회 7일");
  assert.equal(fraction.dose_quantity, 0.5);
  assert.equal(fraction.dose_unit, "정");

  const malformed = parsePrescriptionHints("약명: 테스트정\n1/0정 1일 2회 7일");
  assert.equal(malformed.dose_quantity, null);
});

test("keeps a medication-bag 복용법 line inside that drug block", () => {
  const document = parsePrescriptionDocument([
    box("약명: 테스트정", 20, 10, 220, 30),
    box("복용법: 1회 1정 1일 3회 5일 식후", 20, 40, 430, 65),
  ]);

  assert.equal(document.rows.length, 1);
  assert.deepEqual(
    [document.rows[0].dose_amount, document.rows[0].dose_unit, document.rows[0].frequency_per_day, document.rows[0].prescription_days, document.rows[0].meal_relation],
    [1, "정", 3, 5, "after_meal"],
  );
  assert.equal(document.rows[0].association, "labeled_block");
});

test("does not leak a common regimen to drugs declared after the shared group", () => {
  const document = parsePrescriptionDocument([
    box("약명: A정", 20, 10, 220, 30),
    box("약명: B정", 20, 40, 220, 60),
    box("공통 복용법: 1회 1정 1일 3회 5일", 20, 75, 430, 100),
    box("약명: C정", 20, 140, 220, 160),
    box("1회 복용량: 2정", 20, 170, 230, 190),
  ]);

  assert.equal(document.rows.length, 3);
  assert.deepEqual(document.rows.slice(0, 2).map((row) => [row.frequency_per_day, row.prescription_days]), [[3, 5], [3, 5]]);
  assert.equal(document.rows[2].dose_amount, 2);
  assert.equal(document.rows[2].frequency_per_day, null);
  assert.equal(document.rows[2].prescription_days, null);
  assert.equal(document.rows[2].association, "labeled_block");
});

test("fails catastrophically low-confidence OCR fields closed", () => {
  const document = parsePrescriptionDocument([
    box("약명: 테스트정", 20, 10, 220, 30, 0.99),
    box("5정 1일 2회 7일", 20, 40, 260, 60, 0.01),
  ]);

  assert.equal(document.rows.length, 1);
  assert.equal(document.rows[0].dose_amount, null);
  assert.equal(document.rows[0].frequency_per_day, null);
  assert.equal(document.rows[0].prescription_days, null);
  assert.ok(document.ambiguity_codes.includes("LOW_CONFIDENCE_OCR"));
});
