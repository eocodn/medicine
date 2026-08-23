"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "../..");
const MODULE_URL = pathToFileURL(path.join(ROOT, "browser_ocr/parser-export-package.mjs")).href;
const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

function fixtureManifest(modelBytes) {
  return {
    schema_version: 1,
    status: "ok",
    model_format: "onnx",
    model_file: "parser.onnx",
    model_sha256: sha(modelBytes),
    source_model_result: "/artifacts/parser/models/fixture/result.json",
    source_model_result_sha256: "a".repeat(64),
    source_checkpoint_sha256: "b".repeat(64),
    implementation_sha256: "c".repeat(64),
    parameter_count: 61930,
    architecture: {
      model_id: "sparse_document_graph_v1",
      node_feature_dim: 81,
      edge_feature_dim: 13,
      role_labels: [
        "product", "product_label", "dose", "frequency", "duration",
        "instruction", "schedule", "header", "other",
      ],
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
}

test("runtime packager accepts only hash-bound parser exports", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "medicine-parser-export-"));
  try {
    const modelBytes = Buffer.from("fixture-onnx");
    const manifest = fixtureManifest(modelBytes);
    const manifestBytes = Buffer.from(`${JSON.stringify(manifest)}\n`);
    fs.writeFileSync(path.join(root, "parser.onnx"), modelBytes);
    fs.writeFileSync(path.join(root, "manifest.json"), manifestBytes);

    const { loadParserExport } = await import(`${MODULE_URL}?t=${Date.now()}`);
    const loaded = await loadParserExport(root);
    assert.equal(loaded.binding.enabled, true);
    assert.equal(loaded.binding.model_sha256, sha(modelBytes));
    assert.equal(loaded.binding.export_manifest_sha256, sha(manifestBytes));
    assert.deepEqual(loaded.modelBytes, modelBytes);
    const runtimeManifest = JSON.parse(loaded.runtimeManifestBytes.toString("utf8"));
    assert.equal(runtimeManifest.model_sha256, sha(modelBytes));
    assert.equal(runtimeManifest.source.export_manifest_sha256, sha(manifestBytes));
    assert.equal(runtimeManifest.source.source_model_result_sha256, "a".repeat(64));
    assert.equal(runtimeManifest.source.source_checkpoint_sha256, "b".repeat(64));
    assert.equal(runtimeManifest.source.implementation_sha256, "c".repeat(64));
    assert.equal(Object.hasOwn(runtimeManifest, "source_model_result"), false);
    assert.equal(loaded.runtimeManifestBytes.includes(Buffer.from("/artifacts/")), false);

    fs.writeFileSync(path.join(root, "parser.onnx"), Buffer.from("mutated"));
    await assert.rejects(loadParserExport(root), /model sha256 mismatch/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("runtime packager keeps parser absence explicit", async () => {
  const { disabledParserBinding, loadParserExport } = await import(`${MODULE_URL}?t=${Date.now()}`);
  assert.equal(await loadParserExport(undefined), null);
  assert.deepEqual(disabledParserBinding(), { enabled: false });
});