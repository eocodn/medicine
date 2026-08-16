"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parseDocumentItems } = require("../src/document-parser.js");

function item(id, text, x, y, width = 100, height = 24, score = 1) {
  return {
    id, text, score,
    poly: [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
  };
}

function products(result) { return result.rows.map((row) => row.product_query); }

test("parses a complete prescription table and generic total-days header", () => {
  const result = parseDocumentItems([
    item("h1", "약품명", 20, 20, 120),
    item("h2", "1회 투약량", 220, 20, 130),
    item("h3", "1일 투약 횟수", 420, 20, 150),
    item("h4", "총 일수", 650, 20, 110),
    item("p1", "가나다정", 20, 70, 130),
    item("d1", "1정", 240, 70, 70),
    item("f1", "3회", 450, 70, 70),
    item("t1", "5일", 670, 70, 70),
  ]);

  assert.deepEqual(products(result), ["가나다정"]);
  assert.deepEqual(result.rows[0].draft, {
    dose_amount: 1, dose_unit: "tablet", frequency_per_day: 3, prescription_days: 5,
  });
});

test("structural fallback tolerates a product box touching the dose column", () => {
  const result = parseDocumentItems([
    item("h1", "약품명", 20, 20, 120),
    item("h2", "1회 투약량", 220, 20, 130),
    item("h3", "1일 핏수", 420, 20, 130),
    item("h4", "종 일수", 620, 20, 120),
    item("p1", "가나다정", 20, 70, 225),
    item("d1", "1정", 240, 70, 70),
    item("f1", "3회", 440, 70, 70),
    item("t1", "5일", 640, 70, 70),
    item("p2", "라마바정", 20, 120, 225),
    item("d2", "2정", 240, 120, 70),
    item("f2", "2회", 440, 120, 70),
    item("t2", "7일", 640, 120, 70),
  ]);

  assert.deepEqual(products(result), ["가나다정", "라마바정"]);
  assert.equal(result.rows[0].draft.frequency_per_day, 3);
  assert.equal(result.rows[1].draft.prescription_days, 7);
});

test("table cell uses one typed value even with trailing instruction text", () => {
  const result = parseDocumentItems([
    item("h1", "약품명", 20, 20, 120),
    item("h2", "1회 투약량", 220, 20, 130),
    item("h3", "1일 투약 횟수", 420, 20, 150),
    item("h4", "총 일수", 650, 20, 110),
    item("p1", "가나다정", 20, 70, 130),
    item("d1", "1정", 240, 70, 70),
    item("f1", "3회", 450, 70, 70),
    item("t1", "5일", 670, 70, 60),
    item("i1", "식후 30분", 750, 70, 130),
  ]);

  assert.equal(result.rows[0].draft.prescription_days, 5);
});

test("preserves meaningful spaces inside product names", () => {
  const result = parseDocumentItems([
    item("p1", "타진서방정 10/5mg", 20, 20, 180),
    item("d1", "1정", 240, 20, 70),
    item("f1", "2회", 440, 20, 70),
    item("t1", "5일", 640, 20, 70),
    item("p2", "라마바정", 20, 80, 130),
    item("d2", "2정", 240, 80, 70),
    item("f2", "3회", 440, 80, 70),
    item("t2", "7일", 640, 80, 70),
  ]);

  assert.equal(result.rows[0].product_query, "타진서방정 10/5mg");
  assert.deepEqual(products(result), ["타진서방정 10/5mg", "라마바정"]);
});

test("ambiguous packet-tablet preprint keeps dose amount but leaves unit unresolved", () => {
  const result = parseDocumentItems([
    item("daily", "1일", 20, 20, 60),
    item("freq", "3회", 100, 20, 60),
    item("each", "1회", 200, 20, 60),
    item("dose", "1포(정)", 280, 20, 100),
    item("total", "총", 420, 20, 50),
    item("days", "5일분", 490, 20, 80),
    item("label", "약품명", 20, 170, 80),
    item("product", "메트포르민정", 120, 170, 150),
  ]);

  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].draft.dose_amount, 1);
  assert.equal(result.rows[0].draft.dose_unit, undefined);
  assert.equal(result.rows[0].draft.frequency_per_day, 3);
  assert.equal(result.rows[0].draft.prescription_days, 5);
});

test("table parser does not invent dose from a contaminated product cell", () => {
  const result = parseDocumentItems([
    item("h1", "약품명", 20, 20, 120),
    item("h2", "1회 투약량", 240, 20, 130),
    item("h3", "1일 투약 횟수", 450, 20, 150),
    item("h4", "총 일수", 680, 20, 110),
    item("p1", "타진서방정 10/5mg0.5정", 20, 70, 200),
    item("f1", "3회", 470, 70, 70),
    item("t1", "5일", 700, 70, 70),
  ]);

  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].product_query, "타진서방정 10/5mg0.5정");
  assert.equal(result.rows[0].draft.dose_amount, undefined);
  assert.equal(result.rows[0].draft.dose_unit, undefined);
  assert.equal(result.rows[0].draft.frequency_per_day, 3);
  assert.equal(result.rows[0].draft.prescription_days, 5);
});
