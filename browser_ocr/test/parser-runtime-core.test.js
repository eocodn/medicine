"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { ROLE_LABELS } = require("../src/parser-graph-core.js");
const { runParserModel } = require("../src/parser-runtime-core.js");

class FakeTensor {
  constructor(type, data, dims) {
    this.type = type;
    this.data = data;
    this.dims = dims;
  }
}

const ort = { Tensor: FakeTensor };
const contract = {
  architecture: { neighbor_count: 2 },
  decodeConfig: {
    product_threshold: 0.75,
    product_margin: 0.18,
    field_threshold: 0.62,
    field_margin: 0.10,
    relation_threshold: 0.72,
    relation_margin: 0.12,
  },
};

function poly(x, y, w = 80, h = 24) {
  return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
}

function roleLogits(nodeCount) {
  const values = new Float32Array(nodeCount * ROLE_LABELS.length).fill(-4);
  values[ROLE_LABELS.indexOf("other")] = 4;
  values[ROLE_LABELS.length + ROLE_LABELS.indexOf("product")] = 7;
  values[2 * ROLE_LABELS.length + ROLE_LABELS.indexOf("dose")] = 7;
  return values;
}

test("parser runtime performs role pass then only candidate relation pairs", async () => {
  const calls = [];
  const session = {
    async run(feeds) {
      calls.push(feeds);
      if (calls.length === 1) {
        return {
          role_logits: { data: roleLogits(3) },
          relation_logits: { data: new Float32Array(0) },
        };
      }
      return {
        role_logits: { data: roleLogits(3) },
        relation_logits: { data: new Float32Array([4]) },
      };
    },
  };
  const rows = await runParserModel(session, ort, contract, [
    { id: "p", text: "가나다정", score: 0.98, poly: poly(100, 100, 120) },
    { id: "d", text: "1정", score: 0.97, poly: poly(400, 100) },
  ], 1000, 1400);

  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].relation_index.dims, [0, 2]);
  assert.deepEqual(calls[1].relation_index.dims, [1, 2]);
  assert.ok(calls[1].relation_index.data instanceof BigInt64Array);
  assert.deepEqual(Array.from(calls[1].relation_index.data), [1n, 2n]);
  assert.deepEqual(rows, [{
    row_id: "p",
    product_query: "가나다정",
    draft: { dose_amount: 1, dose_unit: "tablet" },
    uncertainty_codes: [],
  }]);
});

test("parser runtime skips the relation pass when no product candidate is confident", async () => {
  let calls = 0;
  const session = {
    async run() {
      calls += 1;
      return {
        role_logits: { data: new Float32Array(2 * ROLE_LABELS.length) },
        relation_logits: { data: new Float32Array(0) },
      };
    },
  };
  const rows = await runParserModel(session, ort, contract, [
    { id: "x", text: "영수증", score: 0.9, poly: poly(100, 100) },
  ], 1000, 1400);
  assert.equal(calls, 1);
  assert.deepEqual(rows, []);
});