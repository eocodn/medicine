"use strict";

const PAGE_NODE_ID = "__page__";
const TEXT_HASH_DIM = 64;
const NODE_SCALAR_DIM = 17;
const NODE_FEATURE_DIM = TEXT_HASH_DIM + NODE_SCALAR_DIM;
const EDGE_FEATURE_DIM = 13;
const ROLE_LABELS = Object.freeze([
  "product",
  "product_label",
  "dose",
  "frequency",
  "duration",
  "instruction",
  "schedule",
  "header",
  "other",
]);
const FNV1A32_OFFSET = 2166136261;
const FNV1A32_PRIME = 16777619;
const TEXT_ENCODER = new TextEncoder();
const TEXT_HASH_PREFIX = TEXT_ENCODER.encode("med-graph\0");
const DIGIT_RE = /^\p{Nd}$/u;
const ALPHA_RE = /^\p{L}$/u;
const NUMBER_RE = /^\p{N}$/u;
const SPACE_RE = /^\p{White_Space}$/u;

function finiteNumber(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`${label} must be finite`);
  return number;
}

function lexicalCompare(left, right) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function compactText(value) {
  return Array.from(String(value || ""))
    .filter((character) => !SPACE_RE.test(character))
    .join("")
    .toLowerCase();
}

function fnv1a32(bytes) {
  let hash = FNV1A32_OFFSET >>> 0;
  for (const byte of TEXT_HASH_PREFIX) {
    hash ^= byte;
    hash = Math.imul(hash, FNV1A32_PRIME) >>> 0;
  }
  for (const byte of bytes) {
    hash ^= byte;
    hash = Math.imul(hash, FNV1A32_PRIME) >>> 0;
  }
  return hash >>> 0;
}

function hashText(value) {
  const normalized = compactText(value);
  const characters = Array.from(normalized);
  const features = Array(TEXT_HASH_DIM).fill(0);
  let grams = 0;
  for (const width of [1, 2, 3]) {
    for (let index = 0; index + width <= characters.length; index += 1) {
      const raw = fnv1a32(TEXT_ENCODER.encode(characters.slice(index, index + width).join("")));
      features[raw % TEXT_HASH_DIM] += (raw & (1 << 8)) !== 0 ? -1 : 1;
      grams += 1;
    }
  }
  if (!grams) return features;
  const norm = Math.sqrt(features.reduce((sum, item) => sum + item * item, 0)) || 1;
  return features.map((item) => item / norm);
}

function normalizePolygon(value, label) {
  if (!Array.isArray(value) || value.length !== 4) throw new Error(`${label} polygon must contain four points`);
  return value.map((point, index) => {
    if (!Array.isArray(point) || point.length !== 2) throw new Error(`${label} polygon point ${index} must contain x/y`);
    return [finiteNumber(point[0], `${label} polygon x`), finiteNumber(point[1], `${label} polygon y`)];
  });
}

function geometry(polygon) {
  const xs = polygon.map((point) => point[0]);
  const ys = polygon.map((point) => point[1]);
  const x1 = Math.min(...xs);
  const y1 = Math.min(...ys);
  const x2 = Math.max(...xs);
  const y2 = Math.max(...ys);
  const width = Math.max(x2 - x1, 1e-9);
  const height = Math.max(y2 - y1, 1e-9);
  const angle = Math.atan2(polygon[1][1] - polygon[0][1], polygon[1][0] - polygon[0][0]);
  return { cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, width, height, x1, y1, x2, y2, angle };
}

function characterFraction(text, predicate) {
  const characters = Array.from(text);
  if (!characters.length) return 0;
  return characters.filter(predicate).length / characters.length;
}

function nodeFeatures(text, confidence, polygon, documentWidth, documentHeight) {
  const { cx, cy, width, height, x1, y1, angle } = geometry(polygon);
  const characters = Array.from(text);
  const scalar = [
    cx / documentWidth,
    cy / documentHeight,
    width / documentWidth,
    height / documentHeight,
    Math.min(width / Math.max(height, 1e-9), 16) / 16,
    confidence,
    Math.min(characters.length / 64, 1),
    characterFraction(text, (character) => DIGIT_RE.test(character)),
    characterFraction(text, (character) => character >= "가" && character <= "힣"),
    characterFraction(text, (character) => ALPHA_RE.test(character)),
    characterFraction(text, (character) => SPACE_RE.test(character)),
    characterFraction(text, (character) => !ALPHA_RE.test(character)
      && !NUMBER_RE.test(character) && !SPACE_RE.test(character)),
    Math.sin(angle),
    Math.cos(angle),
    Math.min(x1 / documentWidth, 1),
    Math.min(y1 / documentHeight, 1),
    0,
  ];
  const features = [...hashText(compactText(text)), ...scalar];
  if (features.length !== NODE_FEATURE_DIM) throw new Error("parser node feature dimension changed unexpectedly");
  return features;
}

function pageFeatures() {
  const features = Array(NODE_FEATURE_DIM).fill(0);
  features[features.length - 1] = 1;
  return features;
}

function overlap(a1, a2, b1, b2) {
  const intersection = Math.max(0, Math.min(a2, b2) - Math.max(a1, b1));
  return intersection / Math.max(Math.min(a2 - a1, b2 - b1), 1e-9);
}

function edgeFeatures(source, target, documentWidth, documentHeight, pageEdge = false) {
  if (pageEdge) {
    const features = Array(EDGE_FEATURE_DIM).fill(0);
    features[features.length - 1] = 1;
    return features;
  }
  const sourceGeometry = geometry(source.polygon);
  const targetGeometry = geometry(target.polygon);
  const dx = (sourceGeometry.cx - targetGeometry.cx) / documentWidth;
  const dy = (sourceGeometry.cy - targetGeometry.cy) / documentHeight;
  const distance = Math.hypot(dx, dy);
  const widthRatio = Math.log(Math.max(sourceGeometry.width, 1e-9) / Math.max(targetGeometry.width, 1e-9));
  const heightRatio = Math.log(Math.max(sourceGeometry.height, 1e-9) / Math.max(targetGeometry.height, 1e-9));
  return [
    dx,
    dy,
    Math.abs(dx),
    Math.abs(dy),
    distance,
    overlap(sourceGeometry.y1, sourceGeometry.y2, targetGeometry.y1, targetGeometry.y2),
    overlap(sourceGeometry.x1, sourceGeometry.x2, targetGeometry.x1, targetGeometry.x2),
    dx < 0 ? 1 : 0,
    dy < 0 ? 1 : 0,
    Math.max(-2, Math.min(2, widthRatio)) / 2,
    Math.max(-2, Math.min(2, heightRatio)) / 2,
    distance ? Math.abs(Math.atan2(dy, dx)) / Math.PI : 0,
    0,
  ];
}

function normalizedDistance(source, target, width, height) {
  const left = geometry(source.polygon);
  const right = geometry(target.polygon);
  return Math.hypot((left.cx - right.cx) / width, (left.cy - right.cy) / height);
}

function buildParserGraph(items, width, height, neighborCount = 12) {
  const documentWidth = finiteNumber(width, "parser document width");
  const documentHeight = finiteNumber(height, "parser document height");
  if (documentWidth <= 0 || documentHeight <= 0) throw new Error("parser document dimensions must be positive");
  if (!Number.isInteger(neighborCount) || neighborCount < 1 || neighborCount > 32) {
    throw new Error("parser neighbor count must be between 1 and 32");
  }
  if (!Array.isArray(items)) throw new Error("parser OCR items must be an array");

  const page = {
    nodeId: PAGE_NODE_ID,
    text: "",
    confidence: 1,
    polygon: [[0, 0], [documentWidth, 0], [documentWidth, documentHeight], [0, documentHeight]],
    features: pageFeatures(),
  };
  const nodes = [page];
  const nodeIndex = new Map([[PAGE_NODE_ID, 0]]);
  for (const [index, raw] of items.entries()) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`parser OCR item ${index} must be an object`);
    const nodeId = String(raw.id || "");
    if (!nodeId || nodeId === PAGE_NODE_ID || nodeIndex.has(nodeId)) throw new Error("parser OCR ids must be unique and non-empty");
    const confidence = finiteNumber(raw.score, `${nodeId} recognition score`);
    if (confidence < 0 || confidence > 1) throw new Error(`${nodeId} recognition score must be between 0 and 1`);
    const polygon = normalizePolygon(raw.poly, nodeId);
    const text = String(raw.text || "");
    nodeIndex.set(nodeId, nodes.length);
    nodes.push({
      nodeId,
      text,
      confidence,
      polygon,
      features: nodeFeatures(text, confidence, polygon, documentWidth, documentHeight),
    });
  }

  const edges = [];
  const ocrIndices = Array.from({ length: nodes.length - 1 }, (_, index) => index + 1);
  for (const targetIndex of ocrIndices) {
    const target = nodes[targetIndex];
    const ranked = ocrIndices.filter((index) => index !== targetIndex).sort((leftIndex, rightIndex) => {
      const leftDistance = normalizedDistance(nodes[leftIndex], target, documentWidth, documentHeight);
      const rightDistance = normalizedDistance(nodes[rightIndex], target, documentWidth, documentHeight);
      if (leftDistance !== rightDistance) return leftDistance - rightDistance;
      return lexicalCompare(nodes[leftIndex].nodeId, nodes[rightIndex].nodeId);
    });
    for (const sourceIndex of ranked.slice(0, neighborCount)) {
      edges.push({
        source: sourceIndex,
        target: targetIndex,
        features: edgeFeatures(nodes[sourceIndex], target, documentWidth, documentHeight),
      });
    }
    edges.push({ source: 0, target: targetIndex, features: edgeFeatures(page, target, documentWidth, documentHeight, true) });
    edges.push({ source: targetIndex, target: 0, features: edgeFeatures(target, page, documentWidth, documentHeight, true) });
  }

  return {
    width: documentWidth,
    height: documentHeight,
    nodes,
    nodeIds: nodes.map((node) => node.nodeId),
    nodeIndex,
    nodeFeatures: new Float32Array(nodes.flatMap((node) => node.features)),
    edgeIndex: new Int32Array(edges.flatMap((edge) => [edge.source, edge.target])),
    edgeFeatures: new Float32Array(edges.flatMap((edge) => edge.features)),
    edgeCount: edges.length,
  };
}

function relationPairFeatures(graph, productId, fieldId) {
  const productIndex = graph.nodeIndex.get(productId);
  const fieldIndex = graph.nodeIndex.get(fieldId);
  if (!Number.isInteger(productIndex) || !Number.isInteger(fieldIndex)
      || productIndex < 1 || fieldIndex < 1 || productIndex === fieldIndex) {
    throw new Error("parser relation pair must reference two different OCR nodes");
  }
  return edgeFeatures(graph.nodes[productIndex], graph.nodes[fieldIndex], graph.width, graph.height);
}

function buildRelationBatch(graph, pairs) {
  if (!Array.isArray(pairs)) throw new Error("parser relation pairs must be an array");
  const seen = new Set();
  const indices = [];
  const features = [];
  for (const raw of pairs) {
    if (!Array.isArray(raw) || raw.length !== 2) throw new Error("parser relation pair must contain product and field ids");
    const productId = String(raw[0]);
    const fieldId = String(raw[1]);
    const key = `${productId}\0${fieldId}`;
    if (seen.has(key)) throw new Error("parser relation pairs must be unique");
    seen.add(key);
    const productIndex = graph.nodeIndex.get(productId);
    const fieldIndex = graph.nodeIndex.get(fieldId);
    if (!Number.isInteger(productIndex) || !Number.isInteger(fieldIndex)) throw new Error("parser relation pair references unknown OCR node");
    indices.push(productIndex, fieldIndex);
    features.push(...relationPairFeatures(graph, productId, fieldId));
  }
  return {
    relationIndex: new Int32Array(indices),
    relationFeatures: new Float32Array(features),
    relationCount: pairs.length,
  };
}

function validateDecodeConfig(raw) {
  const keys = [
    "product_threshold", "product_margin", "field_threshold", "field_margin",
    "relation_threshold", "relation_margin",
  ];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("parser decode_config must be an object");
  const config = {};
  for (const key of keys) {
    const value = finiteNumber(raw[key], `parser decode_config.${key}`);
    if (value < 0 || value > 1) throw new Error(`parser decode_config.${key} must be between 0 and 1`);
    config[key] = value;
  }
  return config;
}

function validateParserManifest(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("parser manifest must be an object");
  if (raw.schema_version !== 1 || raw.status !== "ok" || raw.model_format !== "onnx") {
    throw new Error("parser manifest is not a supported completed ONNX export");
  }
  if (raw.model_file !== "parser.onnx" || !/^[0-9a-f]{64}$/.test(String(raw.model_sha256 || ""))) {
    throw new Error("parser manifest model binding is invalid");
  }
  const architecture = raw.architecture;
  if (!architecture || typeof architecture !== "object" || Array.isArray(architecture)) {
    throw new Error("parser manifest architecture is missing");
  }
  if (architecture.model_id !== "sparse_document_graph_v1"
      || architecture.node_feature_dim !== NODE_FEATURE_DIM
      || architecture.edge_feature_dim !== EDGE_FEATURE_DIM
      || JSON.stringify(architecture.role_labels) !== JSON.stringify(ROLE_LABELS)) {
    throw new Error("parser manifest architecture does not match the mobile graph contract");
  }
  if (!Number.isInteger(architecture.neighbor_count)
      || architecture.neighbor_count < 1 || architecture.neighbor_count > 32) {
    throw new Error("parser manifest neighbor_count is invalid");
  }
  const inputNames = Array.isArray(raw.inputs) ? raw.inputs.map((item) => item?.name) : [];
  const outputNames = Array.isArray(raw.outputs) ? raw.outputs.map((item) => item?.name) : [];
  if (JSON.stringify(inputNames) !== JSON.stringify([
    "node_features", "edge_index", "edge_features", "relation_index", "relation_features",
  ]) || JSON.stringify(outputNames) !== JSON.stringify(["role_logits", "relation_logits"])) {
    throw new Error("parser manifest ONNX IO contract is invalid");
  }
  return {
    architecture: { ...architecture },
    decodeConfig: validateDecodeConfig(raw.decode_config),
    modelSha256: raw.model_sha256,
  };
}

function roleScoresFromLogits(logits, graph) {
  if (!logits || typeof logits.length !== "number"
      || logits.length !== graph.nodes.length * ROLE_LABELS.length) {
    throw new Error("parser role logits shape does not match graph nodes");
  }
  const result = {};
  for (let nodeIndex = 1; nodeIndex < graph.nodes.length; nodeIndex += 1) {
    const offset = nodeIndex * ROLE_LABELS.length;
    let maximum = -Infinity;
    for (let roleIndex = 0; roleIndex < ROLE_LABELS.length; roleIndex += 1) {
      maximum = Math.max(maximum, Number(logits[offset + roleIndex]));
    }
    const exponentials = ROLE_LABELS.map((_, roleIndex) => Math.exp(Number(logits[offset + roleIndex]) - maximum));
    const denominator = exponentials.reduce((sum, value) => sum + value, 0);
    result[graph.nodes[nodeIndex].nodeId] = Object.fromEntries(
      ROLE_LABELS.map((role, roleIndex) => [role, exponentials[roleIndex] / denominator]),
    );
  }
  return result;
}

module.exports = {
  EDGE_FEATURE_DIM,
  NODE_FEATURE_DIM,
  PAGE_NODE_ID,
  ROLE_LABELS,
  buildParserGraph,
  buildRelationBatch,
  hashText,
  relationPairFeatures,
  roleScoresFromLogits,
  validateParserManifest,
};