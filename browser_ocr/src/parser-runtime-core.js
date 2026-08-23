"use strict";

const {
  EDGE_FEATURE_DIM,
  NODE_FEATURE_DIM,
  ROLE_LABELS,
  buildParserGraph,
  buildRelationBatch,
  roleScoresFromLogits,
} = require("./parser-graph-core.js");
const {
  decodeCandidates,
  decodeParserRows,
  relationScoresFromLogits,
} = require("./parser-decode-core.js");

function int64Values(values) {
  return BigInt64Array.from(values, (value) => BigInt(value));
}

function parserFeeds(ort, graph, relation) {
  return {
    node_features: new ort.Tensor("float32", graph.nodeFeatures, [graph.nodes.length, NODE_FEATURE_DIM]),
    edge_index: new ort.Tensor("int64", int64Values(graph.edgeIndex), [graph.edgeCount, 2]),
    edge_features: new ort.Tensor("float32", graph.edgeFeatures, [graph.edgeCount, EDGE_FEATURE_DIM]),
    relation_index: new ort.Tensor("int64", int64Values(relation.relationIndex), [relation.relationCount, 2]),
    relation_features: new ort.Tensor("float32", relation.relationFeatures, [relation.relationCount, EDGE_FEATURE_DIM]),
  };
}

function outputData(outputs, name, expectedLength) {
  const output = outputs?.[name];
  if (!output || !output.data || output.data.length !== expectedLength) {
    throw new Error(`parser ONNX output ${name} has an unexpected shape`);
  }
  return output.data;
}

async function runParserModel(session, ort, contract, items, width, height) {
  const graph = buildParserGraph(items, width, height, contract.architecture.neighbor_count);
  const emptyRelations = buildRelationBatch(graph, []);
  const first = await session.run(parserFeeds(ort, graph, emptyRelations));
  const roleLogits = outputData(first, "role_logits", graph.nodes.length * ROLE_LABELS.length);
  const roleScores = roleScoresFromLogits(roleLogits, graph);
  const candidates = decodeCandidates(graph, roleScores, contract.decodeConfig);
  const pairs = candidates.products.flatMap((productId) =>
    candidates.fields.map(([fieldId]) => [productId, fieldId]));
  let associationScores = {};
  if (pairs.length) {
    const relationBatch = buildRelationBatch(graph, pairs);
    const second = await session.run(parserFeeds(ort, graph, relationBatch));
    associationScores = relationScoresFromLogits(
      pairs,
      outputData(second, "relation_logits", relationBatch.relationCount),
    );
  }
  return decodeParserRows(graph, roleScores, associationScores, contract.decodeConfig);
}

module.exports = { parserFeeds, runParserModel };