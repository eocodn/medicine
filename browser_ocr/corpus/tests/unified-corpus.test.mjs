import assert from "node:assert/strict";
import { readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtemp } from "node:fs/promises";
import test from "node:test";

import { buildDrugCatalog, buildHistoricalDrugExposure, normalizeDrugName } from "../drug_holdout.mjs";
import { generateUnifiedCorpus } from "../generator.mjs";
import { materializeUnifiedViews } from "../materialize.mjs";
import { validateUnifiedCorpus } from "../contract.mjs";

function lines(text) {
  return text.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

function drugCatalog(count = 240) {
  return buildDrugCatalog(Array.from({ length: count }, (_, index) => ({
    item_seq: String(700000000 + index),
    product_name: `검증약${String(index).padStart(3, "0")}정`,
  })));
}

function historicalExposure(catalog) {
  return buildHistoricalDrugExposure({
    productNames: catalog.slice(0, Math.floor(catalog.length * 0.7)).map((product) => product.product_name),
    checkpointSha256: "b".repeat(64),
    sourceDatasetId: "fixture-selected-recognizer-train",
    sourceDatasetFingerprint: "c".repeat(64),
    sourceTrainSplitSha256: "d".repeat(64),
    sourceTrainSampleCount: 76520,
  });
}

test("one document corpus materializes aligned detection recognition parsing and e2e views", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-ocr-"));
  try {
    const corpusRoot = join(root, "corpus");
    const viewsRoot = join(corpusRoot, "views");
    const catalog = drugCatalog();
    const corpus = await generateUnifiedCorpus({
      outputDir: corpusRoot,
      count: 12,
      seed: 311,
      drugSplitSeed: 161,
      historicalDrugExposure: historicalExposure(catalog),
      drugCatalog: catalog,
    });
    validateUnifiedCorpus(corpus);

    assert.equal(corpus.schema_version, 3);
    assert.deepEqual(corpus.tasks, ["detection", "recognition", "parsing", "e2e"]);
    assert.equal(corpus.generator.version, 5);
    assert.equal(corpus.drug_name_policy.id, "canonical-product-family-historical-holdout-v2");
    assert.equal(corpus.drug_name_policy.historical_exposure.id, "selected-recognizer-training-exposure-v1");
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.augmentation_difficulty)), new Set(["clean", "medium", "hard"]));
    assert.ok(corpus.samples.every((sample) => ["train", "val", "test"].includes(sample.split)));
    assert.ok(corpus.samples.every((sample) => sample.drug_name_split === sample.split));
    assert.ok(corpus.samples.every((sample) => sample.drug_name_exposure === (sample.split === "train" ? "seen" : "unseen")));
    assert.ok(corpus.samples.every((sample) => ["prescription", "medication_bag"].includes(sample.document_type)));

    const productOwners = new Map();
    const familyOwners = new Map();
    for (const sample of corpus.samples) {
      for (const region of sample.regions.filter((item) => item.semantic_role === "product")) {
        assert.equal(region.drug_name_split, sample.split);
        assert.match(region.drug_family, /^family-[0-9a-f]{20}$/);
        const normalized = normalizeDrugName(region.text);
        const productOwner = productOwners.get(normalized);
        if (productOwner) assert.equal(productOwner, sample.split);
        productOwners.set(normalized, sample.split);
        const familyOwner = familyOwners.get(region.drug_family);
        if (familyOwner) assert.equal(familyOwner, sample.split);
        familyOwners.set(region.drug_family, sample.split);
      }
    }

    const leaked = structuredClone(corpus);
    const trainProduct = leaked.samples.find((sample) => sample.split === "train")
      .regions.find((region) => region.semantic_role === "product");
    const testProduct = leaked.samples.find((sample) => sample.split === "test")
      .regions.find((region) => region.semantic_role === "product");
    testProduct.text = trainProduct.text;
    testProduct.drug_family = trainProduct.drug_family;
    assert.throws(() => validateUnifiedCorpus(leaked), /leaks across/);

    const report = await materializeUnifiedViews({
      corpusPath: join(corpusRoot, "manifest.json"),
      outputDir: viewsRoot,
      python: "/opt/detection-venv/bin/python",
    });
    assert.equal(report.status, "completed");
    assert.deepEqual(report.stages, ["detection", "recognition", "parsing", "e2e"]);

    const detection = lines(await readFile(join(viewsRoot, "detection", "samples.jsonl"), "utf8"));
    const recognition = lines(await readFile(join(viewsRoot, "recognition", "index.jsonl"), "utf8"));
    const recognitionSamples = lines(await readFile(join(viewsRoot, "recognition", "samples.jsonl"), "utf8"));
    const parsing = lines(await readFile(join(viewsRoot, "parsing", "samples.jsonl"), "utf8"));
    const e2e = lines(await readFile(join(viewsRoot, "e2e", "samples.jsonl"), "utf8"));

    assert.equal(detection.length, 12);
    const detectorExport = JSON.parse(await readFile(join(viewsRoot, "detection", "paddle", "export.json"), "utf8"));
    assert.equal(detectorExport.parent_corpus_id, corpus.corpus_id);
    for (const name of ["train", "val", "test"]) {
      const detectorLines = (await readFile(join(viewsRoot, "detection", "paddle", `${name}.txt`), "utf8")).trim().split("\n").filter(Boolean);
      assert.equal(detectorLines.length, corpus.samples.filter((sample) => sample.split === name).length);
      for (const line of detectorLines) {
        const [image, label] = line.split("\t");
        assert.ok(image.startsWith("images/"));
        const regions = JSON.parse(label);
        assert.ok(regions.every((region) => region.points.length === 4 && typeof region.transcription === "string"));
      }
    }
    assert.equal(parsing.length, 12);
    assert.equal(e2e.length, 12);
    assert.equal(recognition.length, corpus.samples.reduce((sum, sample) => sum + sample.regions.length, 0));
    assert.equal(recognitionSamples.length, recognition.length);
    const recognitionSampleById = new Map(recognitionSamples.map((sample) => [sample.id, sample]));
    for (const item of recognition) {
      assert.equal(
        recognitionSampleById.get(item.id).risk_tags.includes("critical-medication"),
        item.critical,
      );
    }

    const splitByDocument = new Map(corpus.samples.map((sample) => [sample.id, sample.split]));
    const augmentationByDocument = new Map(corpus.samples.map((sample) => [sample.id, {
      difficulty: sample.augmentation_difficulty,
      components: sample.capture.augmentation_components,
    }]));
    for (const item of [...detection, ...recognition, ...parsing, ...e2e]) {
      assert.equal(item.split, splitByDocument.get(item.document_id));
      assert.equal(item.drug_name_split, splitByDocument.get(item.document_id));
      assert.equal(item.drug_name_exposure, item.split === "train" ? "seen" : "unseen");
      assert.equal(item.augmentation_difficulty, augmentationByDocument.get(item.document_id).difficulty);
      assert.deepEqual(item.augmentation_components, augmentationByDocument.get(item.document_id).components);
    }
    assert.ok(recognition.filter((item) => item.semantic_role === "product").every((item) => /^family-[0-9a-f]{20}$/.test(item.drug_family)));

    const cropState = JSON.parse(await readFile(join(viewsRoot, "recognition", ".crop-state.json"), "utf8"));
    assert.equal(cropState.status, "completed");
    assert.equal(cropState.completed, recognition.length);

    const firstRecognition = recognition[0];
    assert.equal(firstRecognition.text, corpus.samples[0].regions[0].text);
    assert.equal(firstRecognition.source_polygon_kind, "region_polygon");
    const cropInfo = await stat(join(viewsRoot, "recognition", firstRecognition.image));
    assert.ok(cropInfo.isFile());
    assert.match(firstRecognition.image_sha256, /^[0-9a-f]{64}$/);

    const classic = parsing.find((item) => item.layout_family === "classic_medication_bag");
    assert.ok(classic);
    const labeledRow = classic.expected_rows.find((row) => row.row_id === "b0-label");
    assert.ok(labeledRow);
    assert.deepEqual(labeledRow.evidence.product_query, ["b0-label", "b0-product"]);
    assert.equal(classic.nodes.find((node) => node.node_id === "b0-label").semantic_role, "product_label");

    const firstParsing = parsing.find((sample) => sample.positive_edges.length > 0);
    assert.ok(firstParsing);
    const byNode = new Map(firstParsing.nodes.map((node) => [node.node_id, node]));
    for (const edge of firstParsing.positive_edges) {
      assert.equal(byNode.get(edge.product_node_id).semantic_role, "product");
      assert.ok(["dose", "frequency", "duration"].includes(byNode.get(edge.field_node_id).semantic_role));
      assert.equal(byNode.get(edge.product_node_id).association_group, byNode.get(edge.field_node_id).association_group);
    }

    const manifest = JSON.parse(await readFile(join(viewsRoot, "recognition", "manifest.json"), "utf8"));
    assert.equal(manifest.task, "text_recognition");
    assert.equal(manifest.metadata.parent_corpus_id, corpus.corpus_id);
    assert.equal(manifest.metadata.recognition_evaluation_policy.id, "severe-motion-downscale-jpeg-v1");
    const split = JSON.parse(await readFile(join(viewsRoot, "recognition", "document-split.json"), "utf8"));
    assert.equal(split.parent_corpus_id, corpus.corpus_id);
    assert.deepEqual(Object.keys(split.splits).sort(), ["test", "train", "val"]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("unified sample split is stable as corpus scale grows", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-scale-"));
  try {
    const catalog = drugCatalog();
    const exposure = historicalExposure(catalog);
    const small = await generateUnifiedCorpus({ outputDir: join(root, "small"), count: 12, seed: 719, drugSplitSeed: 161, historicalDrugExposure: exposure, drugCatalog: catalog });
    const large = await generateUnifiedCorpus({ outputDir: join(root, "large"), count: 24, seed: 719, drugSplitSeed: 161, historicalDrugExposure: exposure, drugCatalog: catalog });
    const largeByIndex = new Map(large.samples.map((sample) => [sample.sample_index, sample]));
    for (const sample of small.samples) {
      assert.equal(sample.split, largeByIndex.get(sample.sample_index).split);
      assert.equal(sample.layout_family, largeByIndex.get(sample.sample_index).layout_family);
      assert.equal(sample.capture_profile, largeByIndex.get(sample.sample_index).capture_profile);
      assert.equal(sample.augmentation_difficulty, largeByIndex.get(sample.sample_index).augmentation_difficulty);
      assert.deepEqual(sample.capture.augmentation_components, largeByIndex.get(sample.sample_index).capture.augmentation_components);
      assert.equal(sample.drug_name_split, largeByIndex.get(sample.sample_index).drug_name_split);
      assert.deepEqual(
        sample.regions.filter((region) => region.semantic_role === "product").map((region) => [region.text, region.drug_family]),
        largeByIndex.get(sample.sample_index).regions.filter((region) => region.semantic_role === "product").map((region) => [region.text, region.drug_family]),
      );
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("drug split seed stays stable across different document generation seeds", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-drug-seed-"));
  try {
    const catalog = drugCatalog();
    const first = await generateUnifiedCorpus({
      outputDir: join(root, "first"), count: 1, seed: 811, drugSplitSeed: 161, historicalDrugExposure: historicalExposure(catalog), drugCatalog: catalog,
    });
    const second = await generateUnifiedCorpus({
      outputDir: join(root, "second"), count: 1, seed: 812, drugSplitSeed: 161, historicalDrugExposure: historicalExposure(catalog), drugCatalog: catalog,
    });
    assert.equal(first.drug_name_policy.assignment_seed, 161);
    assert.equal(second.drug_name_policy.assignment_seed, 161);
    assert.equal(first.drug_name_policy.assignment_sha256, second.drug_name_policy.assignment_sha256);
    assert.deepEqual(first.drug_name_policy.pools, second.drug_name_policy.pools);
    assert.notEqual(first.generator.fingerprint, second.generator.fingerprint);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
