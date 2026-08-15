import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const MANIFEST_PATH = join(HERE, "detector-models.json");
const SHA256 = /^[0-9a-f]{64}$/;
const OFFICIAL_HOST = "https://paddle-model-ecology.bj.bcebos.com/";
const DETECTOR_EDGES = [640, 960, 1280];

function fail(message) {
  throw new Error(`invalid detector model manifest: ${message}`);
}

function numericArray(value, length, label) {
  if (!Array.isArray(value) || value.length !== length || value.some((item) => !Number.isFinite(item))) {
    fail(`${label} must contain ${length} finite numbers`);
  }
}

function validateModel(name, model) {
  if (!model || typeof model !== "object" || Array.isArray(model)) fail(`${name} must be an object`);
  if (typeof model.archive !== "string" || !model.archive.endsWith("_onnx_infer.tar")) fail(`${name}.archive is invalid`);
  if (typeof model.url !== "string" || !model.url.startsWith(OFFICIAL_HOST)) fail(`${name}.url must use the official Paddle model host`);
  if (!SHA256.test(model.sha256 || "")) fail(`${name}.sha256 must be lowercase SHA-256`);
  for (const key of ["archive_root", "onnx_file", "config_file"]) {
    if (typeof model[key] !== "string" || !model[key] || model[key].includes("..") || model[key].startsWith("/")) {
      fail(`${name}.${key} must be a safe relative path component`);
    }
  }
  if (model.preprocess?.color_mode !== "BGR") fail(`${name}.preprocess.color_mode must be BGR`);
  numericArray(model.preprocess?.mean, 3, `${name}.preprocess.mean`);
  numericArray(model.preprocess?.std, 3, `${name}.preprocess.std`);
  const postprocess = model.postprocess;
  if (!postprocess || typeof postprocess !== "object") fail(`${name}.postprocess is required`);
  for (const key of ["threshold", "box_threshold", "unclip_ratio"]) {
    if (!Number.isFinite(postprocess[key]) || postprocess[key] <= 0) fail(`${name}.postprocess.${key} must be positive`);
  }
  if (!Number.isInteger(postprocess.max_candidates) || postprocess.max_candidates <= 0) {
    fail(`${name}.postprocess.max_candidates must be a positive integer`);
  }
}

export function validateDetectorModelManifest(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("root must be an object");
  if (input.schema_version !== 1) fail("schema_version must be 1");
  if (!input.models || typeof input.models !== "object" || Array.isArray(input.models)) fail("models must be an object");
  const names = Object.keys(input.models);
  if (!names.length) fail("models must not be empty");
  for (const name of names) validateModel(name, input.models[name]);
  return structuredClone(input);
}

export async function loadDetectorModelManifest(path = MANIFEST_PATH) {
  return validateDetectorModelManifest(JSON.parse(await readFile(path, "utf8")));
}

export function benchmarkMatrix(manifest) {
  const modelNames = Object.keys(manifest.models);
  return {
    schema_version: 2,
    models: modelNames,
    detector_edges: DETECTOR_EDGES,
    runs: modelNames.flatMap((model) => DETECTOR_EDGES.map((detector_edge) => ({
      model,
      detector_edge,
      asset_sha256: manifest.models[model].sha256,
      postprocess: structuredClone(manifest.models[model].postprocess),
    }))),
    required_mobile_metrics: ["latency_ms", "peak_memory_bytes", "model_bytes"],
    required_quality_metrics: ["recall", "precision", "critical_box_recall", "merge_errors", "cross_association_merges", "split_errors"],
    timing_scope: "development_cpu_proxy_not_android_release_gate",
  };
}

export const detectorModelManifestPath = MANIFEST_PATH;