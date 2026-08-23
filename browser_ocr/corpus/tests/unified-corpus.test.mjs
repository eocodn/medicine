import assert from "node:assert/strict";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtemp } from "node:fs/promises";
import test from "node:test";

import { buildDrugCatalog, buildHistoricalDrugExposure, normalizeDrugName } from "../drug_holdout.mjs";
import { generateUnifiedCorpus, runConcurrentBatches } from "../generator.mjs";
import { materializeUnifiedViews } from "../materialize.mjs";
import { PARSER_STRUCTURE_VARIANTS } from "../parser_structure.mjs";
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

test("bounded generation batches run concurrently while committing in index order", async () => {
  let active = 0;
  let maxActive = 0;
  const committed = [];
  await runConcurrentBatches({
    start: 2,
    end: 9,
    concurrency: 3,
    async worker(index) {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, (9 - index) * 2));
      active -= 1;
      return `sample-${index}`;
    },
    async onBatch(samples) {
      committed.push(...samples);
    },
  });
  assert.equal(maxActive, 3);
  assert.deepEqual(committed, [
    "sample-2", "sample-3", "sample-4", "sample-5", "sample-6", "sample-7", "sample-8",
  ]);
});

test("parallel unified rendering preserves the serial corpus and image hashes", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-concurrency-"));
  try {
    const catalog = drugCatalog();
    const exposure = historicalExposure(catalog);
    const serial = await generateUnifiedCorpus({
      outputDir: join(root, "serial"), count: 6, seed: 733, drugSplitSeed: 161,
      historicalDrugExposure: exposure, drugCatalog: catalog, renderConcurrency: 1,
    });
    const parallel = await generateUnifiedCorpus({
      outputDir: join(root, "parallel"), count: 6, seed: 733, drugSplitSeed: 161,
      historicalDrugExposure: exposure, drugCatalog: catalog, renderConcurrency: 3,
    });
    assert.deepEqual(parallel, serial);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

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
    assert.equal(corpus.generator.version, 6);
    assert.equal(corpus.drug_name_policy.id, "canonical-product-family-historical-holdout-v2");
    assert.equal(corpus.drug_name_policy.historical_exposure.id, "selected-recognizer-training-exposure-v1");
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.augmentation_difficulty)), new Set(["clean", "medium", "hard"]));
    assert.ok(corpus.samples.every((sample) => ["train", "val", "test"].includes(sample.split)));
    assert.ok(corpus.samples.every((sample) => sample.drug_name_split === sample.split));
    assert.ok(corpus.samples.every((sample) => sample.drug_name_exposure === (sample.split === "train" ? "seen" : "unseen")));
    assert.ok(corpus.samples.every((sample) => ["prescription", "medication_bag"].includes(sample.document_type)));
    assert.ok(corpus.samples.every((sample) => typeof sample.parser_structure_variant === "string" && sample.parser_structure_variant.length > 0));
    assert.ok(corpus.samples.every((sample) => sample.scenario_tags.includes(`parser_structure_${sample.parser_structure_variant}`)));
    const trainStructureVariants = new Set(corpus.samples.filter((sample) => sample.split === "train").map((sample) => sample.parser_structure_variant));
    const valStructureVariants = new Set(corpus.samples.filter((sample) => sample.split === "val").map((sample) => sample.parser_structure_variant));
    const testStructureVariants = new Set(corpus.samples.filter((sample) => sample.split === "test").map((sample) => sample.parser_structure_variant));
    assert.equal([...trainStructureVariants].some((value) => valStructureVariants.has(value) || testStructureVariants.has(value)), false);
    assert.equal([...valStructureVariants].some((value) => testStructureVariants.has(value)), false);

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

    assert.ok(parsing.every((item) => item.image_sha256 && item.width > 0 && item.height > 0));
    assert.ok(parsing.every((item) => Array.isArray(item.scenario_tags) && Array.isArray(item.risk_tags)));
    const parsingByDocument = new Map(parsing.map((item) => [item.document_id, item]));
    const geometrySample = corpus.samples.find((sample) => sample.regions.some((region) => (
      JSON.stringify(region.natural_text_polygon) !== JSON.stringify(region.polygon)
    )));
    assert.ok(geometrySample);
    const geometryRegion = geometrySample.regions.find((region) => (
      JSON.stringify(region.natural_text_polygon) !== JSON.stringify(region.polygon)
    ));
    assert.deepEqual(
      parsingByDocument.get(geometrySample.id).nodes.find((node) => node.node_id === geometryRegion.region_id).natural_text_polygon,
      geometryRegion.natural_text_polygon,
    );

    const parserTrainManifest = JSON.parse(await readFile(join(viewsRoot, "parsing", "datasets", "train-synthetic-ocr", "manifest.json"), "utf8"));
    const parserValManifest = JSON.parse(await readFile(join(viewsRoot, "parsing", "datasets", "val-synthetic-ocr", "manifest.json"), "utf8"));
    const parserOracleManifest = JSON.parse(await readFile(join(viewsRoot, "parsing", "datasets", "oracle", "manifest.json"), "utf8"));
    const parserTrainOracleManifest = JSON.parse(await readFile(join(viewsRoot, "parsing", "datasets", "train-oracle", "manifest.json"), "utf8"));
    assert.equal(parserTrainManifest.task, "medication_document_parser");
    assert.equal(parserTrainManifest.schema_version, 3);
    assert.match(parserTrainManifest.metadata_sha256, /^[0-9a-f]{64}$/);
    assert.equal(parserTrainManifest.document_count, corpus.samples.filter((sample) => sample.split === "train").length);
    assert.equal(parserValManifest.document_count, corpus.samples.filter((sample) => sample.split === "val").length);
    assert.equal(parserOracleManifest.document_count, corpus.samples.length);
    assert.equal(parserTrainOracleManifest.document_count, corpus.samples.filter((sample) => sample.split === "train").length);
    assert.equal(parserTrainOracleManifest.metadata.observation_kind, "oracle");
    assert.equal(parserTrainOracleManifest.metadata.split, "train");
    const parserTrainDocuments = lines(await readFile(join(viewsRoot, "parsing", "datasets", "train-synthetic-ocr", "samples.jsonl"), "utf8"));
    assert.ok(parserTrainDocuments.every((item) => item.split === "train" && item.source_kind === "synthetic"));
    assert.ok(parserTrainDocuments.every((item) => item.observation.kind === "synthetic_ocr"));
    assert.ok(parserTrainDocuments.every((item) => item.annotation_status === "complete"));
    assert.ok(parserTrainDocuments.every((item) => item.gold_rows_reviewed === true));
    assert.ok(parsing.some((item) => item.expected_rows.some((row) => row.draft.meal_relation === "after_meal")));

    const firstParsing = parsing.find((sample) => sample.positive_edges.length > 0);
    assert.ok(firstParsing);
    const byNode = new Map(firstParsing.nodes.map((node) => [node.node_id, node]));
    for (const edge of firstParsing.positive_edges) {
      assert.equal(byNode.get(edge.product_node_id).semantic_role, "product");
      assert.ok(["dose", "frequency", "duration", "instruction", "schedule"].includes(byNode.get(edge.field_node_id).semantic_role));
      assert.equal(byNode.get(edge.product_node_id).association_group, byNode.get(edge.field_node_id).association_group);
    }
    const scheduleParsing = parsing.find((item) => item.nodes.some((node) => node.semantic_role === "schedule" && node.association_group !== "document"));
    assert.ok(scheduleParsing);
    const scheduleNodes = new Set(scheduleParsing.nodes.filter((node) => node.semantic_role === "schedule").map((node) => node.node_id));
    assert.ok(scheduleParsing.positive_edges.some((edge) => scheduleNodes.has(edge.field_node_id)));

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

test("materializer ignores stale lock files but rejects corrupted completed artifacts", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-integrity-"));
  try {
    const corpusRoot = join(root, "corpus");
    const viewsRoot = join(corpusRoot, "views");
    const catalog = drugCatalog();
    const corpus = await generateUnifiedCorpus({
      outputDir: corpusRoot,
      count: 12,
      seed: 991,
      drugSplitSeed: 161,
      historicalDrugExposure: historicalExposure(catalog),
      drugCatalog: catalog,
    });
    await mkdir(viewsRoot, { recursive: true });
    await writeFile(join(viewsRoot, ".materialize.lock"), "stale-lock-from-dead-process\n");
    const first = await materializeUnifiedViews({
      corpusPath: join(corpusRoot, "manifest.json"),
      outputDir: viewsRoot,
    });
    assert.equal(first.status, "completed");
    const reused = await materializeUnifiedViews({
      corpusPath: join(corpusRoot, "manifest.json"),
      outputDir: viewsRoot,
    });
    assert.deepEqual(reused, first);

    await rm(join(viewsRoot, "parsing", "datasets", "train-synthetic-ocr", "manifest.json"), { force: true });
    await assert.rejects(
      materializeUnifiedViews({ corpusPath: join(corpusRoot, "manifest.json"), outputDir: viewsRoot }),
      /completed.*artifact|artifact.*missing|parser dataset/iu,
    );
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
    assert.deepEqual(
      new Set(large.samples.filter((sample) => sample.split === "train").map((sample) => sample.parser_structure_variant)),
      new Set(PARSER_STRUCTURE_VARIANTS.train),
    );
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
