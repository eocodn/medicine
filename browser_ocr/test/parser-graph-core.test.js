"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  EDGE_FEATURE_DIM,
  NODE_FEATURE_DIM,
  ROLE_LABELS,
  buildParserGraph,
  buildRelationBatch,
  relationPairFeatures,
  roleScoresFromLogits,
  validateParserManifest,
} = require("../src/parser-graph-core.js");

const ROOT = path.resolve(__dirname, "../..");
const fixture = JSON.parse(fs.readFileSync(
  path.join(ROOT, "browser_ocr/document_parsing/tests/fixtures/parser_graph_contract.json"),
  "utf8",
));

function nearArray(actual, expected, tolerance = 2e-6) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => {
    assert.ok(Math.abs(value - expected[index]) <= tolerance, `${index}: ${value} != ${expected[index]}`);
  });
}

test("shared parser graph fixture matches Python node, edge, and relation features", () => {
  assert.equal(fixture.node_feature_dim, NODE_FEATURE_DIM);
  assert.equal(fixture.edge_feature_dim, EDGE_FEATURE_DIM);
  assert.deepEqual(fixture.role_labels, ROLE_LABELS);
  const graph = buildParserGraph(fixture.items, fixture.width, fixture.height, fixture.neighbor_count);

  assert.deepEqual(graph.nodeIds, fixture.node_ids);
  nearArray(Array.from(graph.nodeFeatures), fixture.node_features.flat());
  assert.deepEqual(Array.from(graph.edgeIndex), fixture.edge_index.flat());
  nearArray(Array.from(graph.edgeFeatures), fixture.edge_features.flat());
  for (const pair of fixture.relation_pairs) {
    nearArray(relationPairFeatures(graph, ...pair.ids), pair.features);
  }

  const relation = buildRelationBatch(graph, fixture.relation_pairs.map((item) => item.ids));
  assert.deepEqual(Array.from(relation.relationIndex), [1, 2, 1, 3]);
  nearArray(Array.from(relation.relationFeatures), fixture.relation_pairs.flatMap((item) => item.features));
});

test("parser manifest pins the graph and decoder contracts", () => {
  const manifest = {
    schema_version: 1,
    status: "ok",
    model_format: "onnx",
    model_file: "parser.onnx",
    model_sha256: "a".repeat(64),
    architecture: {
      model_id: "sparse_document_graph_v1",
      node_feature_dim: NODE_FEATURE_DIM,
      edge_feature_dim: EDGE_FEATURE_DIM,
      role_labels: [...ROLE_LABELS],
      hidden_dim: 96,
      layers: 2,
      neighbor_count: 12,
      pair_hidden_dim: 64,
    },
    decode_config: {
      product_threshold: 0.75,
      product_margin: 0.18,
      field_threshold: 0.62,
      field_margin: 0.10,
      relation_threshold: 0.72,
      relation_margin: 0.12,
    },
    inputs: ["node_features", "edge_index", "edge_features", "relation_index", "relation_features"]
      .map((name) => ({ name })),
    outputs: ["role_logits", "relation_logits"].map((name) => ({ name })),
  };
  const normalized = validateParserManifest(manifest);
  assert.equal(normalized.architecture.neighbor_count, 12);
  assert.equal(normalized.decodeConfig.relation_threshold, 0.72);

  const wrong = structuredClone(manifest);
  wrong.architecture.node_feature_dim += 1;
  assert.throws(() => validateParserManifest(wrong), /architecture/);
});

test("role logits are converted into per-node normalized probabilities", () => {
  const graph = buildParserGraph(fixture.items.slice(0, 2), fixture.width, fixture.height, 1);
  const logits = new Float32Array(graph.nodes.length * ROLE_LABELS.length);
  logits[ROLE_LABELS.length + ROLE_LABELS.indexOf("product")] = 5;
  logits[2 * ROLE_LABELS.length + ROLE_LABELS.indexOf("dose")] = 4;
  const scores = roleScoresFromLogits(logits, graph);
  assert.deepEqual(Object.keys(scores), ["region-0001", "region-0002"]);
  for (const nodeScores of Object.values(scores)) {
    assert.ok(Math.abs(Object.values(nodeScores).reduce((sum, value) => sum + value, 0) - 1) < 1e-6);
  }
  assert.ok(scores["region-0001"].product > scores["region-0001"].other);
  assert.ok(scores["region-0002"].dose > scores["region-0002"].other);
});