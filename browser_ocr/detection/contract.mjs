const SHA256 = /^[0-9a-f]{64}$/;

function fail(message) {
  throw new Error(`invalid detection corpus: ${message}`);
}

function finiteNumber(value, label) {
  if (!Number.isFinite(value)) fail(`${label} must be finite`);
}

function uniqueStrings(values, label) {
  if (!Array.isArray(values)) fail(`${label} must be an array`);
  const seen = new Set();
  for (const value of values) {
    if (typeof value !== "string" || !value.trim()) fail(`${label} entries must be non-empty strings`);
    if (seen.has(value)) fail(`${label} contains duplicate ${value}`);
    seen.add(value);
  }
}

function validatePolygon(polygon, width, height, label) {
  if (!Array.isArray(polygon) || polygon.length !== 4) fail(`${label} must contain four points`);
  for (const [index, point] of polygon.entries()) {
    if (!Array.isArray(point) || point.length !== 2) fail(`${label}[${index}] must be [x,y]`);
    const [x, y] = point;
    finiteNumber(x, `${label}[${index}].x`);
    finiteNumber(y, `${label}[${index}].y`);
    if (x < 0 || y < 0 || x > width || y > height) fail(`${label}[${index}] is outside image bounds`);
  }
  const crosses = polygon.map((point, index) => {
    const next = polygon[(index + 1) % polygon.length];
    const following = polygon[(index + 2) % polygon.length];
    return (next[0] - point[0]) * (following[1] - next[1])
      - (next[1] - point[1]) * (following[0] - next[0]);
  });
  if (crosses.some((value) => Math.abs(value) < 1e-6)) fail(`${label} must be non-degenerate`);
  if (!(crosses.every((value) => value > 0) || crosses.every((value) => value < 0))) {
    fail(`${label} must be an ordered convex quadrilateral`);
  }
}

function validateGates(gates) {
  if (!gates || typeof gates !== "object" || Array.isArray(gates)) fail("gates must be an object");
  for (const key of ["min_recall", "min_precision", "min_critical_box_recall"]) {
    const value = gates[key];
    finiteNumber(value, `gates.${key}`);
    if (value < 0 || value > 1) fail(`gates.${key} must be between 0 and 1`);
  }
  for (const key of ["max_merge_errors", "max_cross_association_merges", "max_split_errors"]) {
    const value = gates[key];
    if (!Number.isInteger(value) || value < 0) fail(`gates.${key} must be a non-negative integer`);
  }
}

export function validateCorpus(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("root must be an object");
  if (input.schema_version !== 1) fail("schema_version must be 1");
  if (typeof input.corpus_id !== "string" || !input.corpus_id.trim()) fail("corpus_id is required");
  if (typeof input.synthetic_only !== "boolean") fail("synthetic_only must be boolean");
  validateGates(input.gates);
  if (!Array.isArray(input.samples) || input.samples.length === 0) fail("samples must be non-empty");

  const sampleIds = new Set();
  for (const sample of input.samples) {
    if (!sample || typeof sample !== "object" || Array.isArray(sample)) fail("sample must be an object");
    if (typeof sample.id !== "string" || !sample.id.trim()) fail("sample.id is required");
    if (sampleIds.has(sample.id)) fail(`duplicate sample id ${sample.id}`);
    sampleIds.add(sample.id);
    if (typeof sample.image !== "string" || !sample.image.trim() || sample.image.startsWith("/") || sample.image.includes("..")) {
      fail(`${sample.id}.image must be a safe relative path`);
    }
    if (!SHA256.test(sample.image_sha256)) fail(`${sample.id}.image_sha256 must be lowercase SHA-256`);
    if (!Number.isInteger(sample.width) || sample.width <= 0) fail(`${sample.id}.width must be a positive integer`);
    if (!Number.isInteger(sample.height) || sample.height <= 0) fail(`${sample.id}.height must be a positive integer`);
    uniqueStrings(sample.scenario_tags, `${sample.id}.scenario_tags`);
    uniqueStrings(sample.risk_tags, `${sample.id}.risk_tags`);
    if (!Array.isArray(sample.regions) || sample.regions.length === 0) fail(`${sample.id}.regions must be non-empty`);

    const regionIds = new Set();
    for (const region of sample.regions) {
      if (!region || typeof region !== "object" || Array.isArray(region)) fail(`${sample.id}.region must be an object`);
      if (typeof region.region_id !== "string" || !region.region_id.trim()) fail(`${sample.id}.region_id is required`);
      if (regionIds.has(region.region_id)) fail(`${sample.id} has duplicate region ${region.region_id}`);
      regionIds.add(region.region_id);
      if (typeof region.text !== "string" || !region.text.trim()) fail(`${sample.id}.${region.region_id}.text is required`);
      validatePolygon(region.polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.polygon`);
      if (typeof region.critical !== "boolean") fail(`${sample.id}.${region.region_id}.critical must be boolean`);
      if (typeof region.association_group !== "string" || !region.association_group.trim()) {
        fail(`${sample.id}.${region.region_id}.association_group is required`);
      }
      if (typeof region.semantic_role !== "string" || !region.semantic_role.trim()) {
        fail(`${sample.id}.${region.region_id}.semantic_role is required`);
      }
    }
  }
  return structuredClone(input);
}

export function validatePredictions(input, corpus) {
  if (!input || typeof input !== "object" || input.schema_version !== 1) throw new Error("invalid detection predictions: schema_version must be 1");
  if (input.corpus_id !== corpus.corpus_id) throw new Error("invalid detection predictions: corpus_id mismatch");
  if (!Array.isArray(input.samples)) throw new Error("invalid detection predictions: samples must be an array");
  const expectedIds = new Set(corpus.samples.map((sample) => sample.id));
  const seen = new Set();
  for (const sample of input.samples) {
    if (!expectedIds.has(sample.id)) throw new Error(`invalid detection predictions: unknown sample ${sample.id}`);
    if (seen.has(sample.id)) throw new Error(`invalid detection predictions: duplicate sample ${sample.id}`);
    seen.add(sample.id);
    if (!Array.isArray(sample.predictions)) throw new Error(`invalid detection predictions: ${sample.id}.predictions must be an array`);
    const gt = corpus.samples.find((candidate) => candidate.id === sample.id);
    for (const [index, prediction] of sample.predictions.entries()) {
      validatePolygon(prediction.polygon, gt.width, gt.height, `prediction ${sample.id}[${index}].polygon`);
      finiteNumber(prediction.score, `prediction ${sample.id}[${index}].score`);
      if (prediction.score < 0 || prediction.score > 1) throw new Error(`invalid detection predictions: ${sample.id}[${index}].score must be between 0 and 1`);
    }
  }
  if (seen.size !== expectedIds.size) throw new Error("invalid detection predictions: every corpus sample must be present exactly once");
  return structuredClone(input);
}