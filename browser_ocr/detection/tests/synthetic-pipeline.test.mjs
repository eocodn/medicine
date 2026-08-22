import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { auditCoverage } from "../coverage.mjs";
import { homographyFromQuads, transformPoint, transformPolygonToImageBounds } from "../synthetic_capture.mjs";
import { validateCorpus } from "../contract.mjs";
import {
  AUGMENTATION_DIFFICULTIES,
  BACKGROUND_PROFILES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
  REQUIRED_AUGMENTATION_COMPONENTS,
} from "../synthetic_catalog.mjs";
import { generateSyntheticCorpus } from "../synthetic.mjs";
import { generationCheckpointInterval } from "../../corpus/generator.mjs";
import { estimateRenderedTextBox } from "../synthetic_layouts.mjs";
import { testDrugCatalog, testHistoricalDrugExposure } from "../../corpus/tests/fixtures.mjs";

const TEST_DRUG_CATALOG = testDrugCatalog();
const TEST_HISTORICAL_EXPOSURE = testHistoricalDrugExposure();

function generateCorpus(options) {
  return generateSyntheticCorpus({ ...options, drugSplitSeed: 161, historicalDrugExposure: TEST_HISTORICAL_EXPOSURE, drugCatalog: TEST_DRUG_CATALOG });
}

test("homography maps all four source controls to the exact camera-plane controls", () => {
  const source = [[0, 0], [1280, 0], [1280, 1600], [0, 1600]];
  const destination = [[75, 92], [1210, 31], [1260, 1550], [28, 1580]];
  const homography = homographyFromQuads(source, destination);
  for (let index = 0; index < source.length; index += 1) {
    const mapped = transformPoint(homography, source[index]);
    assert.ok(Math.abs(mapped[0] - destination[index][0]) < 1e-4);
    assert.ok(Math.abs(mapped[1] - destination[index][1]) < 1e-4);
  }
});

test("large corpus generation batches resumable checkpoints without unbounded redo", () => {
  assert.equal(generationCheckpointInterval(12), 1);
  assert.equal(generationCheckpointInterval(480), 24);
  assert.equal(generationCheckpointInterval(3600), 50);
});

test("visible transformed quads stay inside the raster when partial crop moves paper off-canvas", () => {
  const homography = [
    1.0541822067410258,
    0.011469369720095154,
    -45.433649585,
    0.021461017128424985,
    1.017305602477879,
    -17.097822573,
    -1.6315570947216407e-06,
    6.976176155425931e-06,
    1,
  ];
  const sourcePolygon = [[39, 266], [176, 266], [176, 342], [39, 342]];
  const visible = transformPolygonToImageBounds(homography, sourcePolygon, 1280, 1600);
  assert.equal(visible.length, 4);
  assert.deepEqual(visible[0], [0, 253.887472845]);
  assert.deepEqual(visible[3], [0, 330.889274948]);
  assert.ok(visible.every(([x, y]) => x >= 0 && x <= 1280 && y >= 0 && y <= 1600));
});

test("synthetic GT tracks rendered text extents rather than layout slot widths", () => {
  const korean = estimateRenderedTextBox("약품명", 34);
  assert.ok(korean.width >= 95 && korean.width <= 115);
  assert.ok(korean.height >= 34 && korean.height <= 42);
  const mixed = estimateRenderedTextBox("1회 투약량", 34);
  assert.ok(mixed.width > korean.width);
  assert.ok(mixed.width < 210);
});

test("legacy bag labels share the regimen association group with their values", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-legacy-association-"));
  try {
    const corpus = await generateCorpus({ outputDir: root, count: 3, seed: 153 });
    const legacy = corpus.samples.find((sample) => sample.layout_family === "legacy_preprinted_medication_bag");
    assert.ok(legacy);
    const groups = new Map(legacy.regions.map((region) => [region.region_id, region.association_group]));
    const medicationGroup = groups.get("product");
    assert.match(medicationGroup, /^med-\d+$/);
    for (const id of ["daily", "frequency", "each", "dose", "days-label", "days", "product-label", "product"]) {
      assert.equal(groups.get(id), medicationGroup);
    }
    assert.ok(legacy.regions.every((region) => region.natural_text_polygon?.length === 4));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("scaled generator covers realistic layout/camera/material strata with rasterized projective GT", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-v2-"));
  try {
    const corpus = await generateCorpus({ outputDir: root, count: 36, seed: 153 });
    assert.equal(corpus.schema_version, 3);
    assert.equal(corpus.generator.version, 6);
    assert.ok(corpus.generator.revision >= 1);
    assert.deepEqual(corpus.tasks, ["detection", "recognition", "parsing", "e2e"]);
    assert.ok(corpus.samples.every((sample) => ["train", "val", "test"].includes(sample.split)));
    assert.equal(corpus.samples.length, 36);
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.layout_family)), new Set(LAYOUT_FAMILIES));
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.capture_profile)), new Set(CAPTURE_PROFILES));
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.augmentation_difficulty)), new Set(AUGMENTATION_DIFFICULTIES));
    const components = new Set(corpus.samples.flatMap((sample) => sample.capture.augmentation_components));
    for (const component of REQUIRED_AUGMENTATION_COMPONENTS) assert.ok(components.has(component), component);
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.material_profile)), new Set(MATERIAL_PROFILES));
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.printer_profile)), new Set(PRINTER_PROFILES));
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.background_profile)), new Set(BACKGROUND_PROFILES));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("glare")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("blur")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("projective_geometry")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("partial_crop")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("clutter")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("jpeg_artifacts")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("printer_degradation")));
    assert.ok(corpus.samples.some((sample) => sample.regions.some((region) => region.region_class === "distractor")));
    const transformed = corpus.samples.find((sample) => sample.capture.geometry_model === "projective");
    assert.ok(transformed);
    assert.equal(transformed.capture.homography.length, 9);
    assert.ok(Math.abs(transformed.capture.homography[6]) > 1e-8 || Math.abs(transformed.capture.homography[7]) > 1e-8);
    assert.ok(transformed.regions.some((region) => JSON.stringify(region.polygon) !== JSON.stringify(region.source_polygon)));
    validateCorpus(corpus);
    assert.match(transformed.image, /\.jpe?g$/);
    const image = await readFile(join(root, transformed.image));
    assert.deepEqual([...image.subarray(0, 2)], [0xff, 0xd8]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("generation resumes exactly after interruption and rejects config drift", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-resume-"));
  let completed = 0;
  try {
    await assert.rejects(
      generateCorpus({
        outputDir: root,
        count: 12,
        seed: 901,
        onProgress(event) {
          completed = event.completed;
          if (event.completed === 4) throw new Error("intentional-stop");
        },
      }),
      /intentional-stop/,
    );
    assert.equal(completed, 4);
    const state = JSON.parse(await readFile(join(root, ".generation-state.json"), "utf8"));
    assert.equal(state.completed, 4);

    await assert.rejects(
      generateCorpus({ outputDir: root, count: 12, seed: 902 }),
      /generation configuration mismatch/,
    );

    const resumed = await generateCorpus({ outputDir: root, count: 12, seed: 901 });
    const freshRoot = join(root, "fresh");
    const fresh = await generateCorpus({ outputDir: freshRoot, count: 12, seed: 901 });
    assert.deepEqual(resumed, fresh);
    await assert.rejects(readFile(join(root, ".generation-state.json"), "utf8"), /ENOENT/);

    await writeFile(join(root, resumed.samples[0].image), "corrupted");
    await assert.rejects(
      generateCorpus({ outputDir: root, count: 12, seed: 901 }),
      /image SHA-256 mismatch/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("generation refuses orphaned output without authoritative state", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-orphan-"));
  try {
    await mkdir(join(root, "images"), { recursive: true });
    await writeFile(join(root, "images/orphan.svg"), "<svg/>");
    await assert.rejects(
      generateCorpus({ outputDir: root, count: 16, seed: 153 }),
      /non-empty without a generation checkpoint/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("coverage audit fails closed when a required synthetic stratum disappears", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-coverage-"));
  try {
    const corpus = await generateCorpus({ outputDir: root, count: 36, seed: 153 });
    const report = auditCoverage(corpus);
    assert.equal(report.status, "pass");
    assert.equal(report.layout_families.length, LAYOUT_FAMILIES.length);
    assert.equal(report.capture_profiles.length, CAPTURE_PROFILES.length);
    assert.equal(report.augmentation_difficulties.length, AUGMENTATION_DIFFICULTIES.length);
    for (const component of REQUIRED_AUGMENTATION_COMPONENTS) assert.ok(report.augmentation_components[component] > 0);
    assert.equal(report.material_profiles.length, MATERIAL_PROFILES.length);
    assert.equal(report.printer_profiles.length, PRINTER_PROFILES.length);
    assert.equal(report.background_profiles.length, BACKGROUND_PROFILES.length);
    assert.ok(report.critical_semantic_roles.product > 0);
    assert.ok(report.critical_semantic_roles.dose > 0);

    const legacy = structuredClone(corpus);
    legacy.generator.version = 5;
    legacy.samples = legacy.samples.filter((sample) => sample.layout_family !== "pharmacy_guide_receipt_sidecar");
    const legacyReport = auditCoverage(legacy);
    assert.equal(legacyReport.status, "pass");
    assert.equal(legacyReport.failures.some((failure) => failure.includes("pharmacy_guide_receipt_sidecar")), false);

    const broken = structuredClone(corpus);
    for (const sample of broken.samples) {
      if (sample.capture_profile === "glare_shadow") sample.capture_profile = "flat_clean";
    }
    const failed = auditCoverage(broken);
    assert.equal(failed.status, "fail");
    assert.ok(failed.failures.some((failure) => failure.includes("glare_shadow")));

    const missingMaterial = structuredClone(corpus);
    for (const sample of missingMaterial.samples) {
      if (sample.material_profile === "plastic_wrinkled") sample.material_profile = "paper_plain";
    }
    const materialFailed = auditCoverage(missingMaterial);
    assert.equal(materialFailed.status, "fail");
    assert.ok(materialFailed.failures.some((failure) => failure.includes("plastic_wrinkled")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});