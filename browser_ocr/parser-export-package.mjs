import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";

const ROLE_LABELS = [
  "product", "product_label", "dose", "frequency", "duration",
  "instruction", "schedule", "header", "other",
];

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function validSha(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function canonicalJsonBytes(value) {
  return Buffer.from(`${JSON.stringify(value)}\n`);
}

function validateParserExportManifest(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)
      || manifest.schema_version !== 1 || manifest.status !== "ok"
      || manifest.model_format !== "onnx" || manifest.model_file !== "parser.onnx") {
    throw new Error("parser export manifest is not a completed ONNX artifact");
  }
  if (!validSha(manifest.model_sha256) || !validSha(manifest.source_model_result_sha256)
      || !validSha(manifest.source_checkpoint_sha256)
      || !validSha(manifest.implementation_sha256)) {
    throw new Error("parser export manifest has invalid SHA-256 bindings");
  }
  const architecture = manifest.architecture;
  if (!architecture || typeof architecture !== "object" || Array.isArray(architecture)
      || architecture.model_id !== "sparse_document_graph_v1"
      || architecture.node_feature_dim !== 81 || architecture.edge_feature_dim !== 13
      || JSON.stringify(architecture.role_labels) !== JSON.stringify(ROLE_LABELS)
      || !Number.isInteger(architecture.hidden_dim) || architecture.hidden_dim < 16 || architecture.hidden_dim > 256
      || !Number.isInteger(architecture.layers) || architecture.layers < 1 || architecture.layers > 6
      || !Number.isInteger(architecture.neighbor_count)
      || architecture.neighbor_count < 1 || architecture.neighbor_count > 32
      || !Number.isInteger(architecture.pair_hidden_dim)
      || architecture.pair_hidden_dim < 16 || architecture.pair_hidden_dim > 256) {
    throw new Error("parser export architecture does not match the mobile runtime contract");
  }
  if (!Number.isInteger(manifest.parameter_count) || manifest.parameter_count < 1 || manifest.parameter_count >= 1_000_000) {
    throw new Error("parser export parameter count is outside the mobile runtime budget");
  }
  const decodeKeys = [
    "product_threshold", "product_margin", "field_threshold", "field_margin",
    "relation_threshold", "relation_margin",
  ];
  if (!manifest.decode_config || typeof manifest.decode_config !== "object"
      || decodeKeys.some((key) => !Number.isFinite(manifest.decode_config[key])
        || manifest.decode_config[key] < 0 || manifest.decode_config[key] > 1)) {
    throw new Error("parser export decoder configuration is invalid");
  }
  const inputs = Array.isArray(manifest.inputs) ? manifest.inputs.map((item) => item?.name) : [];
  const outputs = Array.isArray(manifest.outputs) ? manifest.outputs.map((item) => item?.name) : [];
  if (JSON.stringify(inputs) !== JSON.stringify([
    "node_features", "edge_index", "edge_features", "relation_index", "relation_features",
  ]) || JSON.stringify(outputs) !== JSON.stringify(["role_logits", "relation_logits"])) {
    throw new Error("parser export ONNX IO contract is invalid");
  }
}

export async function loadParserExport(parserExportDirectory) {
  if (!parserExportDirectory) return null;
  const manifestBytes = await readFile(join(parserExportDirectory, "manifest.json"));
  let manifest;
  try {
    manifest = JSON.parse(manifestBytes.toString("utf8"));
  } catch (error) {
    throw new Error(`parser export manifest is invalid JSON: ${error.message}`);
  }
  validateParserExportManifest(manifest);
  const modelBytes = await readFile(join(parserExportDirectory, manifest.model_file));
  if (sha256(modelBytes) !== manifest.model_sha256) throw new Error("parser export model sha256 mismatch");
  const exportManifestSha256 = sha256(manifestBytes);
  const runtimeManifest = {
    schema_version: 1,
    status: "ok",
    model_format: "onnx",
    model_file: "parser.onnx",
    model_sha256: manifest.model_sha256,
    architecture: manifest.architecture,
    decode_config: manifest.decode_config,
    inputs: manifest.inputs,
    outputs: manifest.outputs,
    source: {
      export_manifest_sha256: exportManifestSha256,
      source_model_result_sha256: manifest.source_model_result_sha256,
      source_checkpoint_sha256: manifest.source_checkpoint_sha256,
      implementation_sha256: manifest.implementation_sha256,
    },
  };
  const runtimeManifestBytes = canonicalJsonBytes(runtimeManifest);
  return {
    manifest,
    manifestBytes,
    runtimeManifest,
    runtimeManifestBytes,
    modelBytes,
    binding: {
      enabled: true,
      export_manifest_sha256: exportManifestSha256,
      runtime_manifest_sha256: sha256(runtimeManifestBytes),
      model_sha256: manifest.model_sha256,
      source_model_result_sha256: manifest.source_model_result_sha256,
      source_checkpoint_sha256: manifest.source_checkpoint_sha256,
      implementation_sha256: manifest.implementation_sha256,
    },
  };
}

export function disabledParserBinding() {
  return { enabled: false };
}