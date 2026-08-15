import assert from "node:assert/strict";
import { readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdtemp } from "node:fs/promises";
import test from "node:test";

import { generateUnifiedCorpus } from "../generator.mjs";
import { materializeUnifiedViews } from "../materialize.mjs";
import { validateUnifiedCorpus } from "../contract.mjs";

function lines(text) {
  return text.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
}

test("one document corpus materializes aligned detection recognition parsing and e2e views", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-unified-ocr-"));
  try {
    const corpusRoot = join(root, "corpus");
    const viewsRoot = join(corpusRoot, "views");
    const corpus = await generateUnifiedCorpus({ outputDir: corpusRoot, count: 12, seed: 311 });
    validateUnifiedCorpus(corpus);

    assert.equal(corpus.schema_version, 3);
    assert.deepEqual(corpus.tasks, ["detection", "recognition", "parsing", "e2e"]);
    assert.equal(corpus.generator.version, 4);
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.augmentation_difficulty)), new Set(["clean", "medium", "hard"]));
    assert.ok(corpus.samples.every((sample) => ["train", "val", "test"].includes(sample.split)));
    assert.ok(corpus.samples.every((sample) => ["prescription", "medication_bag"].includes(sample.document_type)));

    const report = await materializeUnifiedViews({
      corpusPath: join(corpusRoot, "manifest.json"),
      outputDir: viewsRoot,
      python: "/opt/detection-venv/bin/python",
    });
    assert.equal(report.status, "completed");
    assert.deepEqual(report.stages, ["detection", "recognition", "parsing", "e2e"]);

    const detection = lines(await readFile(join(viewsRoot, "detection", "samples.jsonl"), "utf8"));
    const recognition = lines(await readFile(join(viewsRoot, "recognition", "index.jsonl"), "utf8"));
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

    const splitByDocument = new Map(corpus.samples.map((sample) => [sample.id, sample.split]));
    const augmentationByDocument = new Map(corpus.samples.map((sample) => [sample.id, {
      difficulty: sample.augmentation_difficulty,
      components: sample.capture.augmentation_components,
    }]));
    for (const item of [...detection, ...recognition, ...parsing, ...e2e]) {
      assert.equal(item.split, splitByDocument.get(item.document_id));
      assert.equal(item.augmentation_difficulty, augmentationByDocument.get(item.document_id).difficulty);
      assert.deepEqual(item.augmentation_components, augmentationByDocument.get(item.document_id).components);
    }

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
    const small = await generateUnifiedCorpus({ outputDir: join(root, "small"), count: 12, seed: 719 });
    const large = await generateUnifiedCorpus({ outputDir: join(root, "large"), count: 24, seed: 719 });
    const largeByIndex = new Map(large.samples.map((sample) => [sample.sample_index, sample]));
    for (const sample of small.samples) {
      assert.equal(sample.split, largeByIndex.get(sample.sample_index).split);
      assert.equal(sample.layout_family, largeByIndex.get(sample.sample_index).layout_family);
      assert.equal(sample.capture_profile, largeByIndex.get(sample.sample_index).capture_profile);
      assert.equal(sample.augmentation_difficulty, largeByIndex.get(sample.sample_index).augmentation_difficulty);
      assert.deepEqual(sample.capture.augmentation_components, largeByIndex.get(sample.sample_index).capture.augmentation_components);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
