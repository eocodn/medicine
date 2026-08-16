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
  assert.equal(corpus.schema_version, 2);
  assert.equal(corpus.synthetic_only, true);
  assert.ok(corpus.samples.length >= 12);
  assert.equal(corpus.gates.critical_token_recall, 1);
  assert.equal(corpus.gates.numeric_token_recall, 1);
  assert.equal(corpus.gates.layout_line_recall, 1);
  assert.equal(corpus.gates.layout_order_accuracy, 1);
  assert.equal(corpus.gates.no_text_sample_pass_rate, 1);
  assert.ok(corpus.gates.max_character_error_rate > 0 && corpus.gates.max_character_error_rate < 0.5);
  assert.ok(corpus.samples.some((sample) => sample.expected_lines?.length >= 3));
  assert.ok(corpus.samples.some((sample) => sample.expect_no_text === true));
  assert.ok(corpus.samples.some((sample) => sample.transform?.blur_px > 0));
  assert.ok(corpus.samples.some((sample) => sample.transform?.scale < 1));
  for (const sample of corpus.samples) {
    assert.match(sample.id, /^[a-z0-9_-]+$/);
    assert.equal(typeof sample.expected_text, "string");
    if (!sample.expect_no_text) assert.ok(sample.critical_tokens.length > 0);
    assert.ok(Array.isArray(sample.numeric_tokens));
    assert.ok(fs.existsSync(path.join(OCR, "eval/corpus", sample.image)), sample.image);
  }
});

test("model evaluation applies declarative degradations and exposes incremental progress", () => {
  const page = fs.readFileSync(path.join(OCR, "eval/eval-page.js"), "utf8");
  const runner = fs.readFileSync(path.join(OCR, "eval/run_eval.mjs"), "utf8");
  assert.match(page, /sample\.transform/);
  assert.match(page, /rotation_degrees/);
  assert.match(page, /blur_px/);
  assert.match(page, /noise/);
  assert.match(page, /__OCR_EVAL_STATE__/);
  assert.match(runner, /__OCR_EVAL_STATE__/);
  assert.match(runner, /checkpoint/);
  assert.match(runner, /completed/);
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

test("web product builder stays detached while Android packages only the approved OCR runtime", () => {
  assert.equal(fs.existsSync(path.join(OCR, "prepare_models.mjs")), false);
  const webDockerfile = fs.readFileSync(path.join(ROOT, "Dockerfile"), "utf8");
  assert.doesNotMatch(webDockerfile, /browser_ocr|browser-ocr|medicine-browser-ocr|export_runtime/);
  const androidDockerfile = fs.readFileSync(path.join(ROOT, "Dockerfile.android"), "utf8");
  assert.match(androidDockerfile, /mobile\/export_runtime\.mjs/);
  assert.match(androidDockerfile, /COPY --from=ocr-assets \/out \/opt\/medicine-ocr-assets/);
  assert.doesNotMatch(androidDockerfile, /browser_ocr\/eval|finetune\/work|ocr-eval/);
  const compose = fs.readFileSync(path.join(ROOT, "compose.yaml"), "utf8");
  assert.doesNotMatch(compose, /MEDICINE_BROWSER_OCR_ASSETS/);
  assert.doesNotMatch(compose, /\n  browser-ocr:/);
  assert.match(compose, /ocr-eval:/);
});

test("model archive names remain confined to the independent OCR project", () => {
  const source = fs.readFileSync(path.join(ROOT, "browser_ocr/Dockerfile"), "utf8");
  assert.doesNotMatch(source, /PP-OCRv5_mobile_det_onnx_infer\.tar/);
  assert.doesNotMatch(source, /korean_PP-OCRv5_mobile_rec_onnx_infer\.tar/);
  assert.match(source, /export_runtime\.mjs \/downloads \/out/);
});

test("Android product packaging exposes only generated on-device OCR assets", () => {
  const build = fs.readFileSync(path.join(ROOT, "android/app/build.gradle.kts"), "utf8");
  const activity = fs.readFileSync(path.join(ROOT, "android/app/src/main/java/com/medicine/android/MainActivity.kt"), "utf8");
  assert.match(build, /PrepareOcrAssets/);
  assert.match(build, /MEDICINE_OCR_ASSETS_DIR/);
  assert.match(activity, /\/ocr-assets\//);
  assert.doesNotMatch(build, /browser_ocr\/eval|finetune\/work/);
});

test("Android OCR builder downloads only the detector archive it actually packages", () => {
  const dockerfile = fs.readFileSync(path.join(ROOT, "Dockerfile.android"), "utf8");
  const fetcher = fs.readFileSync(path.join(OCR, "fetch_assets.mjs"), "utf8");
  assert.match(dockerfile, /fetch_assets\.mjs \/downloads detection/);
  assert.match(fetcher, /requestedSourceNames/);
});
