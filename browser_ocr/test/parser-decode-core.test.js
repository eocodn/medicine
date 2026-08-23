"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { ROLE_LABELS, buildParserGraph } = require("../src/parser-graph-core.js");
const {
  associationKey,
  decodeCandidates,
  decodeParserRows,
  relationScoresFromLogits,
} = require("../src/parser-decode-core.js");

const CONFIG = Object.freeze({
  product_threshold: 0.75,
  product_margin: 0.18,
  field_threshold: 0.62,
  field_margin: 0.10,
  relation_threshold: 0.72,
  relation_margin: 0.12,
});

function poly(x, y, w = 80, h = 24) {
  return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
}

function item(id, text, x, y) {
  return { id, text, score: 0.98, poly: poly(x, y) };
}

function scores(role, primary = 0.9) {
  return Object.fromEntries(ROLE_LABELS.map((label) => [label, label === role ? primary : 0.01]));
}

test("confident learned roles and associations decode typed medication rows", () => {
  const graph = buildParserGraph([
    item("p", "약품명: 가나다정", 100, 100, 140),
    item("d", "1정", 320, 100),
    item("f", "3회", 440, 100),
    item("days", "5일", 540, 100),
    item("i", "식후 복용", 650, 100, 120),
  ], 1000, 1400, 4);
  const roleScores = {
    p: scores("product"),
    d: scores("dose"),
    f: scores("frequency"),
    days: scores("duration"),
    i: scores("instruction"),
  };
  assert.deepEqual(decodeCandidates(graph, roleScores, CONFIG), {
    products: ["p"],
    fields: [["d", "dose"], ["f", "frequency"], ["days", "duration"], ["i", "instruction"]],
  });
  const associationScores = Object.fromEntries(
    ["d", "f", "days", "i"].map((field) => [associationKey("p", field), 0.95]),
  );
  assert.deepEqual(decodeParserRows(graph, roleScores, associationScores, CONFIG), [{
    row_id: "p",
    product_query: "가나다정",
    draft: {
      dose_amount: 1,
      dose_unit: "tablet",
      frequency_per_day: 3,
      prescription_days: 5,
      meal_relation: "after_meal",
      administration_route: "oral",
    },
    uncertainty_codes: [],
  }]);
});

test("association margin failure leaves a field unresolved instead of borrowing it", () => {
  const graph = buildParserGraph([
    item("p1", "가나다정", 100, 100),
    item("p2", "라마바정", 100, 300),
    item("d", "1정", 400, 100),
  ], 1000, 1400, 2);
  const roleScores = { p1: scores("product"), p2: scores("product"), d: scores("dose") };
  const rows = decodeParserRows(graph, roleScores, {
    [associationKey("p1", "d")]: 0.82,
    [associationKey("p2", "d")]: 0.76,
  }, CONFIG);
  assert.deepEqual(rows, [
    { row_id: "p1", product_query: "가나다정", draft: {}, uncertainty_codes: ["AMBIGUOUS_ASSOCIATION"] },
    { row_id: "p2", product_query: "라마바정", draft: {}, uncertainty_codes: [] },
  ]);
});

test("relation logits map to stable sigmoid scores for candidate pairs", () => {
  const pairs = [["p1", "d"], ["p2", "d"]];
  const result = relationScoresFromLogits(pairs, new Float32Array([0, Math.log(3)]));
  assert.equal(result[associationKey("p1", "d")], 0.5);
  assert.ok(Math.abs(result[associationKey("p2", "d")] - 0.75) < 1e-6);
});