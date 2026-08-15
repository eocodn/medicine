import { DRUG_NAME_POLICY_ID, drugExposure, observedDrugLeakageReport } from "./drug_holdout.mjs";

const SHA256 = /^[0-9a-f]{64}$/;
const TASKS = ["detection", "recognition", "parsing", "e2e"];
const SPLITS = new Set(["train", "val", "test"]);
const DOCUMENT_TYPES = new Set(["prescription", "medication_bag"]);

function fail(message) {
  throw new Error(`invalid OCR corpus: ${message}`);
}

export function finiteNumber(value, label) {
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

export function validatePolygon(polygon, width, height, label) {
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

function validateGenerator(generator, schemaVersion) {
  if (!generator || typeof generator !== "object" || Array.isArray(generator)) fail("generator must be an object");
  if (generator.id !== "medicine_full_document_synthetic") fail("generator.id is unsupported");
  if (schemaVersion === 3) {
    if (![4, 5].includes(generator.version)) fail("generator.version must be 4 or 5");
  } else if (generator.version !== 2) {
    fail("generator.version must be 2");
  }
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

function validateSplitPolicy(policy, seed) {
  if (!policy || typeof policy !== "object" || Array.isArray(policy)) fail("split_policy must be an object");
  if (policy.id !== "document-cycle-v1") fail("split_policy.id is unsupported");
  if (policy.seed !== seed) fail("split_policy.seed must match generator.seed");
  const ratios = policy.ratios;
  if (!ratios || typeof ratios !== "object" || Array.isArray(ratios)) fail("split_policy.ratios must be an object");
  if (JSON.stringify(Object.keys(ratios).sort()) !== JSON.stringify(["test", "train", "val"])) {
    fail("split_policy.ratios must contain train, val and test");
  }
  let total = 0;
  for (const name of ["train", "val", "test"]) {
    finiteNumber(ratios[name], `split_policy.ratios.${name}`);
    if (ratios[name] <= 0 || ratios[name] >= 1) fail(`split_policy.ratios.${name} must be between 0 and 1`);
    total += ratios[name];
  }
  if (Math.abs(total - 1) > 1e-9) fail("split_policy.ratios must sum to 1");
}

function validateDrugNamePolicy(policy, splitPolicy) {
  if (!policy || typeof policy !== "object" || Array.isArray(policy)) fail("drug_name_policy must be an object");
  if (policy.id !== DRUG_NAME_POLICY_ID) fail(`drug_name_policy.id must be ${DRUG_NAME_POLICY_ID}`);
  if (!Number.isInteger(policy.assignment_seed)) fail("drug_name_policy.assignment_seed must be an integer");
  const ratios = policy.ratios;
  if (!ratios || typeof ratios !== "object" || Array.isArray(ratios)) fail("drug_name_policy.ratios must be an object");
  if (JSON.stringify(Object.keys(ratios).sort()) !== JSON.stringify(["test", "train", "val"])) {
    fail("drug_name_policy.ratios must contain train, val and test");
  }
  for (const name of ["train", "val", "test"]) {
    finiteNumber(ratios[name], `drug_name_policy.ratios.${name}`);
    if (Math.abs(ratios[name] - splitPolicy.ratios[name]) > 1e-12) fail(`drug_name_policy.ratios.${name} must match split_policy`);
  }
  if (typeof policy.family_rule !== "string" || !policy.family_rule.trim()) fail("drug_name_policy.family_rule is required");
  if (!Number.isInteger(policy.eligible_product_count) || policy.eligible_product_count < 3) fail("drug_name_policy.eligible_product_count must be at least 3");
  if (!Number.isInteger(policy.eligible_family_count) || policy.eligible_family_count < 3) fail("drug_name_policy.eligible_family_count must be at least 3");
  if (!SHA256.test(policy.assignment_sha256)) fail("drug_name_policy.assignment_sha256 must be lowercase SHA-256");
  const source = policy.source;
  if (!source || typeof source !== "object" || Array.isArray(source)) fail("drug_name_policy.source must be an object");
  for (const key of ["dataset_key", "source_family", "source_locator"]) {
    if (typeof source[key] !== "string" || !source[key].trim()) fail(`drug_name_policy.source.${key} is required`);
  }
  for (const key of ["sha256", "canonical_db_sha256"]) {
    if (!SHA256.test(source[key])) fail(`drug_name_policy.source.${key} must be lowercase SHA-256`);
  }
  const pools = policy.pools;
  if (!pools || typeof pools !== "object" || Array.isArray(pools)
    || JSON.stringify(Object.keys(pools).sort()) !== JSON.stringify(["test", "train", "val"])) {
    fail("drug_name_policy.pools must contain train, val and test");
  }
  let productTotal = 0;
  for (const name of ["train", "val", "test"]) {
    const pool = pools[name];
    if (!pool || typeof pool !== "object" || Array.isArray(pool)) fail(`drug_name_policy.pools.${name} must be an object`);
    for (const key of ["product_count", "family_count"]) {
      if (!Number.isInteger(pool[key]) || pool[key] <= 0) fail(`drug_name_policy.pools.${name}.${key} must be positive`);
    }
    if (!SHA256.test(pool.product_names_sha256)) fail(`drug_name_policy.pools.${name}.product_names_sha256 must be lowercase SHA-256`);
    if (!SHA256.test(pool.families_sha256)) fail(`drug_name_policy.pools.${name}.families_sha256 must be lowercase SHA-256`);
    productTotal += pool.product_count;
  }
  if (productTotal !== policy.eligible_product_count) fail("drug_name_policy pool product counts must sum to eligible_product_count");
}

function validateCapture(capture, sampleId, captureProfile, requireComposableAugmentation = false) {
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
  uniqueStrings(capture.risk_tags, `${sampleId}.capture.risk_tags`);
  if (requireComposableAugmentation) {
    if (!["clean", "medium", "hard"].includes(capture.difficulty)) fail(`${sampleId}.capture.difficulty is invalid`);
    uniqueStrings(capture.augmentation_components, `${sampleId}.capture.augmentation_components`);
    for (const key of ["downscale_factor", "sensor_noise", "red_gain", "blue_gain"]) {
      finiteNumber(capture[key], `${sampleId}.capture.${key}`);
    }
    if (capture.downscale_factor < 0.45 || capture.downscale_factor > 1) fail(`${sampleId}.capture.downscale_factor is outside [0.45,1]`);
    if (capture.sensor_noise < 0 || capture.sensor_noise > 0.35) fail(`${sampleId}.capture.sensor_noise is outside [0,0.35]`);
    if (capture.red_gain < 0.82 || capture.red_gain > 1.18) fail(`${sampleId}.capture.red_gain is outside [0.82,1.18]`);
    if (capture.blue_gain < 0.82 || capture.blue_gain > 1.18) fail(`${sampleId}.capture.blue_gain is outside [0.82,1.18]`);
    if (!Number.isInteger(capture.noise_seed) || capture.noise_seed < 0 || capture.noise_seed > 4294967295) {
      fail(`${sampleId}.capture.noise_seed must be an unsigned 32-bit integer`);
    }
    if (capture.jpeg_quality < 42 || capture.jpeg_quality > 96) fail(`${sampleId}.capture.jpeg_quality is outside [42,96]`);
  }
}

export function validateUnifiedCorpus(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("root must be an object");
  if (![1, 2, 3].includes(input.schema_version)) fail("schema_version must be 1, 2 or 3");
  const enhancedSynthetic = input.schema_version >= 2;
  const unified = input.schema_version === 3;
  if (typeof input.corpus_id !== "string" || !input.corpus_id.trim()) fail("corpus_id is required");
  if (typeof input.synthetic_only !== "boolean") fail("synthetic_only must be boolean");
  if (enhancedSynthetic) {
    if (!input.synthetic_only) fail("procedural corpus must declare synthetic_only=true");
    validateGenerator(input.generator, input.schema_version);
    if (!input.provenance || input.provenance.kind !== "procedural_synthetic" || input.provenance.patient_data !== false) {
      fail("provenance must declare procedural_synthetic with patient_data=false");
    }
  }
  if (unified) {
    if (!Array.isArray(input.tasks) || JSON.stringify(input.tasks) !== JSON.stringify(TASKS)) {
      fail(`tasks must be exactly ${TASKS.join(", ")}`);
    }
    validateSplitPolicy(input.split_policy, input.generator.seed);
    if (input.generator.version >= 5) validateDrugNamePolicy(input.drug_name_policy, input.split_policy);
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
      validateCapture(sample.capture, sample.id, sample.capture_profile, unified && input.generator.version >= 4);
    }
    if (unified) {
      if (!SPLITS.has(sample.split)) fail(`${sample.id}.split must be train, val or test`);
      if (!DOCUMENT_TYPES.has(sample.document_type)) fail(`${sample.id}.document_type is unsupported`);
      if (!["clean", "medium", "hard"].includes(sample.augmentation_difficulty)) fail(`${sample.id}.augmentation_difficulty is invalid`);
      if (sample.capture.difficulty !== sample.augmentation_difficulty) fail(`${sample.id}.augmentation_difficulty must match capture.difficulty`);
      if (input.generator.version >= 5) {
        if (!SPLITS.has(sample.drug_name_split)) fail(`${sample.id}.drug_name_split must be train, val or test`);
        if (sample.drug_name_split !== sample.split) fail(`${sample.id}.drug_name_split must match split`);
        if (sample.drug_name_exposure !== drugExposure(sample.drug_name_split)) {
          fail(`${sample.id}.drug_name_exposure does not match drug_name_split`);
        }
      }
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
      if (enhancedSynthetic) {
        validatePolygon(region.source_polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.source_polygon`);
        validatePolygon(region.source_natural_text_polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.source_natural_text_polygon`);
        validatePolygon(region.natural_text_polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.natural_text_polygon`);
      }
      validatePolygon(region.polygon, sample.width, sample.height, `${sample.id}.${region.region_id}.polygon`);
      if (typeof region.critical !== "boolean") fail(`${sample.id}.${region.region_id}.critical must be boolean`);
      if (typeof region.association_group !== "string" || !region.association_group.trim()) fail(`${sample.id}.${region.region_id}.association_group is required`);
      if (typeof region.semantic_role !== "string" || !region.semantic_role.trim()) fail(`${sample.id}.${region.region_id}.semantic_role is required`);
      if (enhancedSynthetic) {
        if (!["medication", "context", "distractor"].includes(region.region_class)) fail(`${sample.id}.${region.region_id}.region_class is invalid`);
        if (!Number.isInteger(region.font_size_px) || region.font_size_px <= 0) fail(`${sample.id}.${region.region_id}.font_size_px must be a positive integer`);
      }
      if (unified && input.generator.version >= 5 && region.semantic_role === "product") {
        if (region.drug_name_split !== sample.drug_name_split) fail(`${sample.id}.${region.region_id}.drug_name_split must match parent document`);
        if (typeof region.drug_family !== "string" || !/^family-[0-9a-f]{20}$/u.test(region.drug_family)) {
          fail(`${sample.id}.${region.region_id}.drug_family is invalid`);
        }
      }
    }
  }
  if (unified && input.generator.version >= 5) {
    const leakage = observedDrugLeakageReport(input.samples);
    if (leakage.status !== "pass") fail(leakage.failures[0] || "drug-name leakage detected");
  }
  return structuredClone(input);
}

export const validateCorpus = validateUnifiedCorpus;
