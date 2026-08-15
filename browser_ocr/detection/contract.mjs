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

function validateGenerator(generator) {
  if (!generator || typeof generator !== "object" || Array.isArray(generator)) fail("generator must be an object");
  if (generator.id !== "medicine_full_document_synthetic") fail("generator.id is unsupported");
  if (generator.version !== 2) fail("generator.version must be 2");
  if (!Number.isInteger(generator.revision) || generator.revision <= 0) fail("generator.revision must be a positive integer");
  if (!Number.isInteger(generator.seed)) fail("generator.seed must be an integer");
  if (!Number.isInteger(generator.count) || generator.count <= 0) fail("generator.count must be a positive integer");
  if (!SHA256.test(generator.fingerprint)) fail("generator.fingerprint must be lowercase SHA-256");
  if (!generator.rasterizer || typeof generator.rasterizer !== "object" || Array.isArray(generator.rasterizer)) fail("generator.rasterizer must be an object");
  if (generator.rasterizer.engine !== "imagemagick-convert") fail("generator.rasterizer.engine is unsupported");
  if (typeof generator.rasterizer.version !== "string" || !generator.rasterizer.version.trim()) fail("generator.rasterizer.version is required");
  if (typeof generator.rasterizer.svg_delegate !== "string" || !generator.rasterizer.svg_delegate.trim()) fail("generator.rasterizer.svg_delegate is required");
  if (!SHA256.test(generator.rasterizer.fingerprint)) fail("generator.rasterizer.fingerprint must be lowercase SHA-256");
}

function validateCapture(capture, sampleId, captureProfile) {
  if (!capture || typeof capture !== "object" || Array.isArray(capture)) fail(`${sampleId}.capture must be an object`);
  if (capture.profile !== captureProfile) fail(`${sampleId}.capture.profile must match capture_profile`);
  if (!["projective", "homography_affine"].includes(capture.geometry_model)) fail(`${sampleId}.capture.geometry_model is invalid`);
  for (const key of ["defocus_radius", "motion_blur_radius", "motion_blur_angle", "contrast", "brightness", "jpeg_quality", "glare_opacity", "shadow_opacity"]) {
    finiteNumber(capture[key], `${sampleId}.capture.${key}`);
  }
  if (!Array.isArray(capture.homography) || capture.homography.length !== 9) fail(`${sampleId}.capture.homography must have nine coefficients`);
  for (const [index, value] of capture.homography.entries()) finiteNumber(value, `${sampleId}.capture.homography[${index}]`);
  for (const key of ["source_corners", "destination_corners"]) {
    if (!Array.isArray(capture[key]) || capture[key].length !== 4) fail(`${sampleId}.capture.${key} must contain four points`);
    for (const [index, point] of capture[key].entries()) {
      if (!Array.isArray(point) || point.length !== 2) fail(`${sampleId}.capture.${key}[${index}] must be [x,y]`);
      finiteNumber(point[0], `${sampleId}.capture.${key}[${index}].x`);
      finiteNumber(point[1], `${sampleId}.capture.${key}[${index}].y`);
    }
  }
  uniqueStrings(capture.camera_failure_modes, `${sampleId}.capture.camera_failure_modes`);
  uniqueStrings(capture.risk_tags, `${sampleId}.capture.risk_tags`);
  if (capture.defocus_radius < 0 || capture.defocus_radius > 5) fail(`${sampleId}.capture.defocus_radius is out of range`);
  if (capture.motion_blur_radius < 0 || capture.motion_blur_radius > 20) fail(`${sampleId}.capture.motion_blur_radius is out of range`);
  if (capture.contrast <= 0 || capture.contrast > 2) fail(`${sampleId}.capture.contrast is out of range`);
  if (capture.brightness <= 0 || capture.brightness > 2) fail(`${sampleId}.capture.brightness is out of range`);
  if (!Number.isInteger(capture.jpeg_quality) || capture.jpeg_quality < 20 || capture.jpeg_quality > 100) fail(`${sampleId}.capture.jpeg_quality is out of range`);
  if (capture.glare_opacity < 0 || capture.glare_opacity > 1) fail(`${sampleId}.capture.glare_opacity is out of range`);
  if (capture.shadow_opacity < 0 || capture.shadow_opacity > 1) fail(`${sampleId}.capture.shadow_opacity is out of range`);
}

export function validateCorpus(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("root must be an object");
  if (![1, 2].includes(input.schema_version)) fail("schema_version must be 1 or 2");
  const enhancedSynthetic = input.schema_version === 2;
  if (typeof input.corpus_id !== "string" || !input.corpus_id.trim()) fail("corpus_id is required");
  if (typeof input.synthetic_only !== "boolean") fail("synthetic_only must be boolean");
  if (enhancedSynthetic) {
    if (!input.synthetic_only) fail("schema v2 is reserved for procedural synthetic corpora");
    validateGenerator(input.generator);
    if (!input.provenance || input.provenance.kind !== "procedural_synthetic" || input.provenance.patient_data !== false) {
      fail("provenance must declare procedural_synthetic with patient_data=false");
    }
  }
  validateGates(input.gates);
  if (!Array.isArray(input.samples) || input.samples.length === 0) fail("samples must be non-empty");
  if (enhancedSynthetic && input.generator.count !== input.samples.length) fail("generator.count must equal samples.length");

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
    if (enhancedSynthetic) {
      if (typeof sample.layout_family !== "string" || !sample.layout_family.trim()) fail(`${sample.id}.layout_family is required`);
      if (typeof sample.capture_profile !== "string" || !sample.capture_profile.trim()) fail(`${sample.id}.capture_profile is required`);
      if (typeof sample.material_profile !== "string" || !sample.material_profile.trim()) fail(`${sample.id}.material_profile is required`);
      if (typeof sample.printer_profile !== "string" || !sample.printer_profile.trim()) fail(`${sample.id}.printer_profile is required`);
      if (typeof sample.background_profile !== "string" || !sample.background_profile.trim()) fail(`${sample.id}.background_profile is required`);
      if (!Number.isInteger(sample.sample_index) || sample.sample_index < 0) fail(`${sample.id}.sample_index must be a non-negative integer`);
      validateCapture(sample.capture, sample.id, sample.capture_profile);
    }
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
      if (enhancedSynthetic) validatePolygon(region.source_polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.source_polygon`);
      validatePolygon(region.polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.polygon`);
      if (typeof region.critical !== "boolean") fail(`${sample.id}.${region.region_id}.critical must be boolean`);
      if (typeof region.association_group !== "string" || !region.association_group.trim()) {
        fail(`${sample.id}.${region.region_id}.association_group is required`);
      }
      if (typeof region.semantic_role !== "string" || !region.semantic_role.trim()) {
        fail(`${sample.id}.${region.region_id}.semantic_role is required`);
      }
      if (enhancedSynthetic) {
        if (!["medication", "context", "distractor"].includes(region.region_class)) {
          fail(`${sample.id}.${region.region_id}.region_class is invalid`);
        }
        if (!Number.isInteger(region.font_size_px) || region.font_size_px <= 0) {
          fail(`${sample.id}.${region.region_id}.font_size_px must be a positive integer`);
        }
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