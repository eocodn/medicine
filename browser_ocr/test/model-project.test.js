"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..", "..");
const OCR = path.join(ROOT, "browser_ocr");

function readJson(relative) {
  return JSON.parse(fs.readFileSync(path.join(OCR, relative), "utf8"));
}

test("OCR model sources are versioned and cryptographically pinned outside application code", () => {
  const manifest = readJson("model-manifest.json");
  assert.equal(manifest.schema_version, 1);
  assert.match(manifest.model_id, /^[a-z0-9._-]+$/);
  assert.equal(manifest.runtime.backend, "onnxruntime-web-wasm");
  assert.equal(manifest.runtime.recognizer, "korean_PP-OCRv5_mobile_rec");
  assert.equal(Object.keys(manifest.sources).length, 2);
  for (const source of Object.values(manifest.sources)) {
    assert.match(source.url, /^https:\/\//);
    assert.match(source.archive, /\.tar$/);
    assert.match(source.sha256, /^[0-9a-f]{64}$/);
  }
  const fetcher = fs.readFileSync(path.join(OCR, "fetch_assets.mjs"), "utf8");
  assert.match(fetcher, /model-manifest\.json/);
  assert.doesNotMatch(fetcher, /781056046c9ed77a15c94681605db6a0f62317c2e9cce6931c71da2478d4bc30/);
});

test("deployment export is an explicit runtime allowlist and excludes evaluation material", async () => {
  const layout = await import(`${pathToFileURL(path.join(OCR, "runtime-layout.mjs")).href}?t=${Date.now()}`);
  assert.deepEqual([...layout.RUNTIME_FILES].sort(), [
    "direct/ocr-worker.js",
    "licenses/THIRD_PARTY_NOTICES.md",
    "licenses/js-yaml-MIT.txt",
    "models/detection.onnx",
    "models/korean-recognition-dictionary.json",
    "models/korean-recognition.onnx",
    "ort/ort-wasm-simd-threaded.mjs",
    "ort/ort-wasm-simd-threaded.wasm",
    "runtime-manifest.json",
  ]);
  for (const name of layout.RUNTIME_FILES) assert.doesNotMatch(name, /(?:eval|corpus|report|fetch|package|test)/i);
  const exporter = fs.readFileSync(path.join(OCR, "export_runtime.mjs"), "utf8");
  assert.match(exporter, /RUNTIME_FILES/);
});

test("checked-in model corpus has no patient data and declares model-level release gates", () => {
  const corpus = readJson("eval/corpus/manifest.json");
  assert.equal(corpus.schema_version, 1);
  assert.equal(corpus.synthetic_only, true);
  assert.ok(corpus.samples.length >= 3);
  assert.equal(corpus.gates.critical_token_recall, 1);
  assert.equal(corpus.gates.numeric_token_recall, 1);
  assert.ok(corpus.gates.max_character_error_rate > 0 && corpus.gates.max_character_error_rate < 0.5);
  for (const sample of corpus.samples) {
    assert.match(sample.id, /^[a-z0-9_-]+$/);
    assert.ok(sample.expected_text);
    assert.ok(sample.critical_tokens.length > 0);
    assert.ok(Array.isArray(sample.numeric_tokens));
    assert.ok(fs.existsSync(path.join(OCR, "eval/corpus", sample.image)), sample.image);
  }
});

test("independent OCR Docker pipeline exposes separate eval and trimmed runtime targets", () => {
  const dockerfile = fs.readFileSync(path.join(OCR, "Dockerfile"), "utf8");
  assert.match(dockerfile, /AS model-build/);
  assert.match(dockerfile, /AS eval/);
  assert.match(dockerfile, /FROM scratch AS runtime/);
  assert.match(dockerfile, /COPY --from=model-build \/out \/ocr-assets/);
  const compose = fs.readFileSync(path.join(ROOT, "compose.yaml"), "utf8");
  assert.match(compose, /ocr-eval:/);
  assert.match(compose, /dockerfile: browser_ocr\/Dockerfile/);
  assert.match(compose, /target: eval/);
});

test("application builders consume only the exported runtime bundle", () => {
  assert.equal(fs.existsSync(path.join(OCR, "prepare_models.mjs")), false);
  for (const name of ["Dockerfile", "Dockerfile.android"]) {
    const source = fs.readFileSync(path.join(ROOT, name), "utf8");
    assert.match(source, /export_runtime\.mjs/);
    assert.match(source, /COPY --from=browser-ocr \/out \/opt\/medicine-browser-ocr/);
    assert.doesNotMatch(source, /prepare_models\.mjs/);
    assert.doesNotMatch(source, /browser_ocr\/(?:eval|test|corpus)/);
  }
});

test("deployment builders do not duplicate model archive names outside the manifest", () => {
  for (const name of ["browser_ocr/Dockerfile", "Dockerfile", "Dockerfile.android"]) {
    const source = fs.readFileSync(path.join(ROOT, name), "utf8");
    assert.doesNotMatch(source, /PP-OCRv5_mobile_det_onnx_infer\.tar/);
    assert.doesNotMatch(source, /korean_PP-OCRv5_mobile_rec_onnx_infer\.tar/);
    assert.match(source, /export_runtime\.mjs \/downloads \/out/);
  }
});

test("Android declares exported OCR runtime as a tracked generated asset input", () => {
  const build = fs.readFileSync(path.join(ROOT, "android/app/build.gradle.kts"), "utf8");
  assert.match(build, /@get:InputDirectory/);
  assert.match(build, /abstract val ocrAssets: DirectoryProperty/);
  assert.match(build, /PrepareOcrAssets/);
  assert.match(build, /addGeneratedSourceDirectory\(prepareOcrAssets, PrepareOcrAssets::outputDirectory\)/);
  assert.doesNotMatch(build, /addStaticSourceDirectory\(file\(ocrAssetsDir\.get\(\)\)/);
});
