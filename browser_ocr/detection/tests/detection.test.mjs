import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runDetectorBenchmarkMatrix } from "../benchmark.mjs";
import { validateCorpus } from "../contract.mjs";
import { benchmarkMatrix, loadDetectorModelManifest } from "../detector_models.mjs";
import { evaluateDetections } from "../evaluation.mjs";
import { generateSyntheticCorpus } from "../synthetic.mjs";

function tinyCorpus() {
  const identityCapture = {
    profile: "flat_scan",
    geometry_model: "homography_affine",
    source_corners: [[0, 0], [200, 0], [200, 100], [0, 100]],
    destination_corners: [[0, 0], [200, 0], [200, 100], [0, 100]],
    homography: [1, 0, 0, 0, 1, 0, 0, 0, 1],
    defocus_radius: 0,
    motion_blur_radius: 0,
    motion_blur_angle: 0,
    contrast: 1,
    brightness: 1,
    jpeg_quality: 92,
    glare_opacity: 0,
    shadow_opacity: 0,
    camera_failure_modes: [],
    risk_tags: [],
  };
  const taggedRegion = (value) => ({
    ...value,
    source_polygon: value.polygon.map((point) => [...point]),
    source_natural_text_polygon: value.polygon.map((point) => [...point]),
    natural_text_polygon: value.polygon.map((point) => [...point]),
    region_class: value.critical ? "medication" : "context",
    font_size_px: 24,
  });
  return {
    schema_version: 2,
    corpus_id: "tiny",
    synthetic_only: true,
    generator: {
      id: "medicine_full_document_synthetic",
      version: 2,
      revision: 2,
      seed: 1,
      count: 1,
      fingerprint: "b".repeat(64),
      rasterizer: { engine: "imagemagick-convert", version: "Version: ImageMagick test", svg_delegate: "rsvg-convert test", fingerprint: "c".repeat(64) },
    },
    provenance: { kind: "procedural_synthetic", patient_data: false },
    gates: {
      min_recall: 1,
      min_precision: 1,
      min_critical_box_recall: 1,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples: [{
      id: "sample",
      image: "sample.jpg",
      image_sha256: "a".repeat(64),
      width: 200,
      height: 100,
      sample_index: 0,
      layout_family: "prescription_table",
      capture_profile: "flat_scan",
      material_profile: "paper_plain",
      printer_profile: "laser_clean",
      background_profile: "desk_light",
      capture: identityCapture,
      scenario_tags: ["prescription_table"],
      risk_tags: ["row_association"],
      regions: [
        taggedRegion({ region_id: "a", text: "약A", polygon: [[10, 10], [80, 10], [80, 35], [10, 35]], critical: true, association_group: "row-a", semantic_role: "product" }),
        taggedRegion({ region_id: "b", text: "1정", polygon: [[100, 10], [150, 10], [150, 35], [100, 35]], critical: true, association_group: "row-a", semantic_role: "dose" }),
        taggedRegion({ region_id: "c", text: "약B", polygon: [[10, 55], [80, 55], [80, 80], [10, 80]], critical: true, association_group: "row-b", semantic_role: "product" }),
      ],
    }],
  };
}

test("contract validates strict full-document quadrilateral corpus", () => {
  const corpus = validateCorpus(tinyCorpus());
  assert.equal(corpus.samples[0].regions.length, 3);
  const broken = tinyCorpus();
  broken.samples[0].regions[0].polygon[0] = [-1, 10];
  assert.throws(() => validateCorpus(broken), /outside image bounds/);

  const genericV1 = tinyCorpus();
  genericV1.schema_version = 1;
  genericV1.synthetic_only = false;
  delete genericV1.generator;
  delete genericV1.provenance;
  for (const sample of genericV1.samples) {
    delete sample.sample_index;
    delete sample.layout_family;
    delete sample.capture_profile;
    delete sample.material_profile;
    delete sample.printer_profile;
    delete sample.background_profile;
    delete sample.capture;
    for (const region of sample.regions) {
      delete region.source_polygon;
      delete region.source_natural_text_polygon;
      delete region.natural_text_polygon;
      delete region.region_class;
      delete region.font_size_px;
    }
  }
  assert.equal(validateCorpus(genericV1).schema_version, 1);
});

test("detector candidates are official pinned ONNX assets with model-specific DB settings", async () => {
  const manifest = await loadDetectorModelManifest();
  assert.equal(manifest.schema_version, 1);
  assert.deepEqual(Object.keys(manifest.models), [
    "PP-OCRv5_mobile_det",
    "PP-OCRv6_tiny_det",
    "PP-OCRv6_small_det",
  ]);
  assert.deepEqual(manifest.models["PP-OCRv5_mobile_det"].postprocess, {
    threshold: 0.3,
    box_threshold: 0.6,
    max_candidates: 1000,
    unclip_ratio: 1.5,
  });
  assert.deepEqual(manifest.models["PP-OCRv6_tiny_det"].postprocess, {
    threshold: 0.2,
    box_threshold: 0.4,
    max_candidates: 3000,
    unclip_ratio: 1.4,
  });
  assert.deepEqual(manifest.models["PP-OCRv6_small_det"].postprocess, {
    threshold: 0.2,
    box_threshold: 0.45,
    max_candidates: 3000,
    unclip_ratio: 1.4,
  });
  for (const model of Object.values(manifest.models)) {
    assert.match(model.url, /^https:\/\/paddle-model-ecology\.bj\.bcebos\.com\//);
    assert.match(model.archive, /_onnx_infer\.tar$/);
    assert.match(model.sha256, /^[0-9a-f]{64}$/);
    assert.equal(model.preprocess.color_mode, "BGR");
    assert.deepEqual(model.preprocess.mean, [0.485, 0.456, 0.406]);
    assert.deepEqual(model.preprocess.std, [0.229, 0.224, 0.225]);
  }

  const matrix = benchmarkMatrix(manifest);
  assert.equal(matrix.runs.length, 9);
  assert.deepEqual(matrix.detector_edges, [640, 960, 1280]);
  assert.ok(matrix.runs.every((run) => run.postprocess && run.asset_sha256));
});

test("detector benchmark refuses concurrent writers for the same output", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-benchmark-lock-"));
  try {
    const outputDir = join(root, "output");
    await mkdir(join(outputDir, "runs"), { recursive: true });
    await writeFile(join(outputDir, ".benchmark.lock"), "occupied\n");
    const corpusPath = join(root, "corpus.json");
    await writeFile(corpusPath, `${JSON.stringify(tinyCorpus())}\n`);
    await assert.rejects(
      runDetectorBenchmarkMatrix({
        corpusPath,
        cacheDir: join(root, "cache"),
        outputDir,
        models: ["PP-OCRv5_mobile_det"],
        detectorEdges: [640],
      }),
      /already running/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("synthetic generator is deterministic and emits valid full documents", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-"));
  try {
    const first = join(root, "a");
    const second = join(root, "b");
    const a = await generateSyntheticCorpus({ outputDir: first, count: 6, seed: 153 });
    const b = await generateSyntheticCorpus({ outputDir: second, count: 6, seed: 153 });
    assert.deepEqual(a, b);
    assert.equal(a.samples.length, 6);
    assert.ok(a.samples.every((sample) => sample.width === 1280 && sample.height === 1600));
    assert.ok(a.samples.some((sample) => sample.scenario_tags.includes("multi_medication")));
    const image = await readFile(join(first, a.samples[0].image));
    assert.deepEqual([...image.subarray(0, 2)], [0xff, 0xd8]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("evaluation distinguishes perfect detections, merges, and critical misses", () => {
  const corpus = validateCorpus(tinyCorpus());
  const perfect = {
    schema_version: 1,
    corpus_id: "tiny",
    samples: [{ id: "sample", predictions: corpus.samples[0].regions.map((region) => ({ polygon: region.polygon, score: 0.99 })) }],
  };
  const good = evaluateDetections(corpus, perfect);
  assert.equal(good.status, "pass");
  assert.equal(good.metrics.critical_box_recall, 1);
  assert.equal(good.metrics.merge_errors, 0);

  const merged = structuredClone(perfect);
  merged.samples[0].predictions = [
    { polygon: [[5, 5], [155, 5], [155, 85], [5, 85]], score: 0.99 },
  ];
  const badMerge = evaluateDetections(corpus, merged);
  assert.equal(badMerge.status, "fail");
  assert.ok(badMerge.metrics.merge_errors >= 1);
  assert.ok(badMerge.metrics.cross_association_merges >= 1);

  const missed = structuredClone(perfect);
  missed.samples[0].predictions.pop();
  const badRecall = evaluateDetections(corpus, missed);
  assert.equal(badRecall.status, "fail");
  assert.ok(badRecall.metrics.critical_box_recall < 1);
});

test("evaluation matches by visible text coverage while still rejecting merged regions", () => {
  const corpus = validateCorpus(tinyCorpus());
  const padded = {
    schema_version: 1,
    corpus_id: "tiny",
    samples: [{
      id: "sample",
      predictions: corpus.samples[0].regions.map((region) => {
        const [[x1, y1], [x2], [, y2]] = region.natural_text_polygon;
        return {
          polygon: [[x1 - 8, y1 - 8], [x2 + 8, y1 - 8], [x2 + 8, y2 + 8], [x1 - 8, y2 + 8]],
          score: 0.99,
        };
      }),
    }],
  };
  const good = evaluateDetections(corpus, padded);
  assert.equal(good.metrics.recall, 1);
  assert.equal(good.metrics.critical_box_recall, 1);

  const merged = structuredClone(padded);
  merged.samples[0].predictions = [
    { polygon: [[5, 5], [155, 5], [155, 85], [5, 85]], score: 0.99 },
  ];
  const unsafe = evaluateDetections(corpus, merged);
  assert.ok(unsafe.metrics.merge_errors >= 1);
  assert.ok(unsafe.metrics.cross_association_merges >= 1);
});