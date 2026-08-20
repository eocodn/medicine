import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, open, readFile, rename, rm, stat, unlink, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";

import { validateUnifiedCorpus } from "./contract.mjs";
import { RECOGNITION_EVAL_POLICY, recognitionOodTag } from "./evaluation_policy.mjs";
import { buildOracleManifest, buildParsingItems, expectedRows } from "./parser_truth.mjs";

const MATERIALIZER_VERSION = 9;
const STAGES = ["detection", "recognition", "parsing", "e2e"];
const STATE_FILE = ".materialize-state.json";
const LOCK_FILE = ".materialize.lock";

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function fileSha256(path) {
  return sha256(await readFile(path));
}

async function atomicJson(path, value) {
  const temporary = `${path}.tmp-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`);
  await rename(temporary, path);
}

function jsonl(items) {
  return `${items.map((item) => JSON.stringify(item)).join("\n")}\n`;
}

function slug(value) {
  const normalized = String(value).toLowerCase().replace(/_/g, "-").replace(/[^a-z0-9.:-]+/g, "-").replace(/^-+|-+$/g, "");
  return normalized || "other";
}

function drugFamilyByAssociation(sample) {
  return new Map(sample.regions
    .filter((region) => region.semantic_role === "product" && region.drug_family)
    .map((region) => [region.association_group, region.drug_family]));
}

function recognitionDrugFamily(sample, region, families) {
  const family = region.drug_family || families.get(region.association_group);
  if (family) return family;
  return `context-${sha256(sample.id).slice(0, 12)}`;
}

function run(command, args, cwd, { streamStderr = false } = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderr += text;
      if (streamStderr) process.stderr.write(text);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolvePromise({ stdout, stderr });
      else reject(new Error(`${command} failed with exit ${code}: ${stderr.trim().slice(-4000)}`));
    });
  });
}

function progress(stage, completed, total) {
  process.stderr.write(`[ocr-corpus] ${stage} ${completed}/${total}\n`);
}

async function writeStageManifest(stageDir, value) {
  await mkdir(stageDir, { recursive: true });
  await atomicJson(join(stageDir, "manifest.json"), value);
}

export async function materializeUnifiedViews({ corpusPath, outputDir, python = "/opt/detection-venv/bin/python" }) {
  const manifestPath = resolve(corpusPath);
  const corpusRoot = dirname(manifestPath);
  const output = resolve(outputDir);
  const corpusRaw = await readFile(manifestPath, "utf8");
  const corpus = validateUnifiedCorpus(JSON.parse(corpusRaw));
  if (corpus.schema_version !== 3) throw new Error("unified view materialization requires OCR corpus schema v3");
  const profile = {
    schema_version: 1,
    materializer_version: MATERIALIZER_VERSION,
    corpus_id: corpus.corpus_id,
    corpus_sha256: sha256(corpusRaw),
    stages: STAGES,
  };
  await mkdir(output, { recursive: true });
  const lockPath = join(output, LOCK_FILE);
  let lock;
  try {
    lock = await open(lockPath, "wx");
  } catch (error) {
    if (error?.code === "EEXIST") throw new Error("unified corpus materialization is already running for this output directory");
    throw error;
  }
  const statePath = join(output, STATE_FILE);
  const reportPath = join(output, "report.json");
  try {
    try {
      const state = JSON.parse(await readFile(statePath, "utf8"));
      if (JSON.stringify(state.profile) !== JSON.stringify(profile)) throw new Error("materialization profile differs from existing state");
      if (state.status === "completed") return JSON.parse(await readFile(reportPath, "utf8"));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const entries = (await Promise.all(STAGES.map(async (stage) => {
        try { await stat(join(output, stage)); return stage; } catch { return null; }
      }))).filter(Boolean);
      if (entries.length) throw new Error("materialization output contains stage data without authoritative state");
    }
    await atomicJson(statePath, { schema_version: 1, status: "running", profile, stage: "detection" });

    const detectionDir = join(output, "detection");
    await mkdir(detectionDir, { recursive: true });
    const detectionItems = corpus.samples.map((sample) => ({
      document_id: sample.id,
      split: sample.split,
      drug_name_split: sample.drug_name_split,
      drug_name_exposure: sample.drug_name_exposure,
      image: sample.image,
      width: sample.width,
      height: sample.height,
      layout_family: sample.layout_family,
      parser_structure_variant: sample.parser_structure_variant,
      capture_profile: sample.capture_profile,
      augmentation_difficulty: sample.augmentation_difficulty,
      augmentation_components: sample.capture.augmentation_components,
      regions: sample.regions.map((region) => ({
        region_id: region.region_id,
        polygon: region.polygon,
        natural_text_polygon: region.natural_text_polygon,
        critical: region.critical,
        semantic_role: region.semantic_role,
        association_group: region.association_group,
        ...(region.drug_family ? {
          drug_family: region.drug_family,
          drug_name_split: region.drug_name_split,
        } : {}),
      })),
    }));
    await writeFile(join(detectionDir, "samples.jsonl"), jsonl(detectionItems));
    const detectorPaddleDir = join(detectionDir, "paddle");
    await mkdir(detectorPaddleDir, { recursive: true });
    const detectorCounts = {};
    for (const name of ["train", "val", "test"]) {
      const selected = detectionItems.filter((item) => item.split === name);
      detectorCounts[name] = selected.length;
      await writeFile(join(detectionDir, `${name}.jsonl`), jsonl(selected));
      const labels = selected.map((item) => {
        const regions = corpus.samples.find((sample) => sample.id === item.document_id).regions.map((region) => ({
          transcription: region.text,
          points: region.polygon,
        }));
        return `${item.image}\t${JSON.stringify(regions)}`;
      });
      await writeFile(join(detectorPaddleDir, `${name}.txt`), `${labels.join("\n")}\n`);
    }
    await atomicJson(join(detectorPaddleDir, "export.json"), {
      schema_version: 1,
      task: "text_detection",
      parent_corpus_id: corpus.corpus_id,
      parent_corpus_sha256: profile.corpus_sha256,
      data_dir: corpusRoot,
      label_files: { train: "train.txt", val: "val.txt", test: "test.txt" },
      counts: detectorCounts,
      polygon_kind: "region_polygon",
      transcription_policy: "ground_truth_text",
    });
    await writeStageManifest(detectionDir, {
      schema_version: 1, stage: "detection", parent_corpus_id: corpus.corpus_id,
      source_corpus: relative(detectionDir, manifestPath), samples_file: "samples.jsonl",
      paddle_export: "paddle/export.json",
    });
    progress("detection", detectionItems.length, detectionItems.length);

    await atomicJson(statePath, { schema_version: 1, status: "running", profile, stage: "recognition" });
    const recognitionDir = join(output, "recognition");
    const recognitionImages = join(recognitionDir, "images");
    await mkdir(recognitionImages, { recursive: true });
    const recognitionSamples = [];
    const recognitionIndex = [];
    const assignments = { train: [], val: [], test: [] };
    const cropJobs = [];
    for (const sample of corpus.samples) {
      const drugFamilies = drugFamilyByAssociation(sample);
      for (const [regionIndex, region] of sample.regions.entries()) {
        const id = `rec-${String(sample.sample_index + 1).padStart(6, "0")}-${String(regionIndex + 1).padStart(4, "0")}`;
        const imageRel = `images/${sample.id}/region-${String(regionIndex + 1).padStart(4, "0")}.png`;
        const outputPath = join(recognitionDir, imageRel);
        cropJobs.push({ image: join(corpusRoot, sample.image), polygon: region.polygon, output: outputPath });
        recognitionIndex.push({
          id, document_id: sample.id, region_id: region.region_id, split: sample.split,
          drug_name_split: sample.drug_name_split,
          drug_name_exposure: sample.drug_name_exposure,
          drug_family: recognitionDrugFamily(sample, region, drugFamilies),
          image: imageRel, text: region.text, semantic_role: region.semantic_role,
          association_group: region.association_group, region_class: region.region_class,
          critical: region.critical, layout_family: sample.layout_family,
          capture_profile: sample.capture_profile,
          augmentation_difficulty: sample.augmentation_difficulty,
          augmentation_components: sample.capture.augmentation_components,
          source_polygon_kind: "region_polygon",
        });
        assignments[sample.split].push(id);
      }
    }
    const jobsPath = join(output, ".recognition-crop-jobs.json");
    await atomicJson(jobsPath, { schema_version: 1, jobs: cropJobs });
    await run(python, [
      "-m", "browser_ocr.corpus.materialize_helpers", "crop",
      "--jobs", jobsPath,
      "--state", join(recognitionDir, ".crop-state.json"),
    ], process.cwd(), { streamStderr: true });
    await rm(jobsPath, { force: true });
    for (const item of recognitionIndex) {
      const region = corpus.samples.find((sample) => sample.id === item.document_id).regions.find((candidate) => candidate.region_id === item.region_id);
      const sample = corpus.samples.find((candidate) => candidate.id === item.document_id);
      const imageHash = await fileSha256(join(recognitionDir, item.image));
      item.image_sha256 = imageHash;
      const oodTag = recognitionOodTag(sample.capture);
      recognitionSamples.push({
        id: item.id,
        image: item.image,
        image_sha256: imageHash,
        text: item.text,
        origin: "synthetic",
        document_type: sample.document_type,
        document_id: sample.id,
        groups: {
          layout_family: slug(sample.layout_family),
          source_family: slug(`capture-${sample.capture_profile}`),
          drug_family: item.drug_family,
        },
        semantic_tags: [slug(region.semantic_role)],
        risk_tags: [...new Set([
          ...sample.risk_tags.map(slug),
          `difficulty-${slug(sample.augmentation_difficulty)}`,
          ...sample.capture.augmentation_components.map((component) => `augmentation-${slug(component)}`),
          `region-${slug(region.region_class)}`,
          ...(region.critical ? ["critical-medication"] : []),
          ...(oodTag ? [oodTag] : []),
          ...(sample.drug_name_split ? [`drug-split-${slug(sample.drug_name_split)}`] : []),
          ...(sample.drug_name_exposure ? [`drug-exposure-${slug(sample.drug_name_exposure)}`] : []),
        ])],
        privacy: { contains_patient_data: false, deidentified: true },
        provenance: {
          source_id: corpus.corpus_id,
          license_id: "procedural-synthetic",
          generator_version: String(corpus.generator.version),
          source_revision: corpus.generator.fingerprint,
        },
      });
    }
    await writeFile(join(recognitionDir, "samples.jsonl"), jsonl(recognitionSamples));
    await writeFile(join(recognitionDir, "index.jsonl"), jsonl(recognitionIndex));
    for (const name of ["train", "val", "test"]) {
      await writeFile(join(recognitionDir, `${name}.jsonl`), jsonl(recognitionIndex.filter((item) => item.split === name)));
    }
    await atomicJson(join(recognitionDir, "manifest.json"), {
      schema_version: 1,
      dataset_id: `${corpus.corpus_id}-recognition`,
      task: "text_recognition",
      patient_data_policy: "forbid",
      samples_file: "samples.jsonl",
      description: "Recognition crops rectified from degraded unified full-document synthetic images",
      metadata: {
        parent_corpus_id: corpus.corpus_id,
        parent_corpus_sha256: profile.corpus_sha256,
        crop_polygon_kind: "region_polygon",
        document_split_policy: corpus.split_policy,
        recognition_evaluation_policy: RECOGNITION_EVAL_POLICY,
        ...(corpus.drug_name_policy ? { drug_name_policy: corpus.drug_name_policy } : {}),
      },
    });
    const assignmentPath = join(recognitionDir, ".split-assignments.json");
    await atomicJson(assignmentPath, {
      schema_version: 1, parent_corpus_id: corpus.corpus_id, seed: corpus.split_policy.seed,
      ratios: corpus.split_policy.ratios, document_count: corpus.samples.length, splits: assignments,
    });
    await run(python, [
      "-m", "browser_ocr.corpus.materialize_helpers", "finalize-recognition",
      "--manifest", join(recognitionDir, "manifest.json"),
      "--assignments", assignmentPath,
      "--paddle-output", join(recognitionDir, "paddle"),
    ], process.cwd());
    await rm(assignmentPath, { force: true });
    progress("recognition", recognitionSamples.length, recognitionSamples.length);

    await atomicJson(statePath, { schema_version: 1, status: "running", profile, stage: "parsing" });
    const parsingDir = join(output, "parsing");
    await mkdir(parsingDir, { recursive: true });
    const parsingItems = buildParsingItems(corpus);
    await writeFile(join(parsingDir, "samples.jsonl"), jsonl(parsingItems));
    const oracleManifest = buildOracleManifest(corpus);
    await atomicJson(join(parsingDir, "oracle-manifest.json"), oracleManifest);
    for (const name of ["train", "val", "test"]) {
      const selected = parsingItems.filter((item) => item.split === name);
      await writeFile(join(parsingDir, `${name}.jsonl`), jsonl(selected));
      await atomicJson(join(parsingDir, `oracle-${name}.json`), {
        schema_version: 2,
        cases: oracleManifest.cases.filter((item) => corpus.samples.find((sample) => sample.id === item.case_id)?.split === name),
      });
    }
    await writeStageManifest(parsingDir, {
      schema_version: 1, stage: "parsing", parent_corpus_id: corpus.corpus_id,
      samples_file: "samples.jsonl", oracle_manifest: "oracle-manifest.json",
      labels: ["semantic_role", "association_group", "same_medication"],
      training_datasets: {
        oracle: "datasets/oracle/manifest.json",
        train_synthetic_ocr: "datasets/train-synthetic-ocr/manifest.json",
        val_synthetic_ocr: "datasets/val-synthetic-ocr/manifest.json",
        test_synthetic_ocr: "datasets/test-synthetic-ocr/manifest.json",
      },
    });
    const parserDatasetSpecs = [
      { name: "oracle", observation: "oracle", split: null },
      { name: "train-synthetic-ocr", observation: "synthetic_ocr", split: "train" },
      { name: "val-synthetic-ocr", observation: "synthetic_ocr", split: "val" },
      { name: "test-synthetic-ocr", observation: "synthetic_ocr", split: "test" },
    ];
    for (const spec of parserDatasetSpecs) {
      const datasetArgs = [
        "-m", "browser_ocr.document_parsing.dataset_cli", "build-synthetic",
        "--truth-samples", join(parsingDir, "samples.jsonl"),
        "--output-dir", join(parsingDir, "datasets", spec.name),
        "--dataset-id", `parser-${corpus.generator.version}-${corpus.generator.seed}-${spec.name}`,
        "--observation-kind", spec.observation,
        "--seed", String(corpus.generator.seed),
        "--json",
      ];
      if (spec.split) datasetArgs.push("--split", spec.split);
      await run(python, datasetArgs, process.cwd(), { streamStderr: true });
    }
    progress("parsing", parsingItems.length, parsingItems.length);

    await atomicJson(statePath, { schema_version: 1, status: "running", profile, stage: "e2e" });
    const e2eDir = join(output, "e2e");
    await mkdir(e2eDir, { recursive: true });
    const e2eItems = corpus.samples.map((sample) => ({
      document_id: sample.id,
      split: sample.split,
      drug_name_split: sample.drug_name_split,
      drug_name_exposure: sample.drug_name_exposure,
      image: sample.image,
      layout_family: sample.layout_family,
      parser_structure_variant: sample.parser_structure_variant,
      capture_profile: sample.capture_profile,
      augmentation_difficulty: sample.augmentation_difficulty,
      augmentation_components: sample.capture.augmentation_components,
      expected_rows: expectedRows(sample),
      critical_region_ids: sample.regions.filter((region) => region.critical).map((region) => region.region_id),
    }));
    await writeFile(join(e2eDir, "samples.jsonl"), jsonl(e2eItems));
    for (const name of ["train", "val", "test"]) {
      await writeFile(join(e2eDir, `${name}.jsonl`), jsonl(e2eItems.filter((item) => item.split === name)));
    }
    await writeStageManifest(e2eDir, {
      schema_version: 1, stage: "e2e", parent_corpus_id: corpus.corpus_id,
      source_corpus: relative(e2eDir, manifestPath), samples_file: "samples.jsonl",
    });
    progress("e2e", e2eItems.length, e2eItems.length);

    const report = {
      schema_version: 1,
      status: "completed",
      corpus_id: corpus.corpus_id,
      corpus_sha256: profile.corpus_sha256,
      stages: STAGES,
      documents: corpus.samples.length,
      regions: recognitionSamples.length,
      splits: Object.fromEntries(["train", "val", "test"].map((name) => [name, corpus.samples.filter((sample) => sample.split === name).length])),
      drug_name_splits: Object.fromEntries(["train", "val", "test"].map((name) => [
        name,
        corpus.samples.filter((sample) => sample.drug_name_split === name).length,
      ])),
      drug_name_exposure: Object.fromEntries(["seen", "unseen"].map((name) => [
        name,
        corpus.samples.filter((sample) => sample.drug_name_exposure === name).length,
      ])),
      recognition: {
        samples: recognitionSamples.length,
        manifest: "recognition/manifest.json",
        split: "recognition/document-split.json",
        paddle_export: "recognition/paddle/export.json",
        crop_polygon_kind: "region_polygon",
      },
      parsing: {
        samples: parsingItems.length,
        oracle_manifest: "parsing/oracle-manifest.json",
        oracle_splits: { train: "parsing/oracle-train.json", val: "parsing/oracle-val.json", test: "parsing/oracle-test.json" },
        training_datasets: {
          oracle: "parsing/datasets/oracle/manifest.json",
          train_synthetic_ocr: "parsing/datasets/train-synthetic-ocr/manifest.json",
          val_synthetic_ocr: "parsing/datasets/val-synthetic-ocr/manifest.json",
          test_synthetic_ocr: "parsing/datasets/test-synthetic-ocr/manifest.json",
        },
      },
      detection: {
        split_files: { train: "detection/train.jsonl", val: "detection/val.jsonl", test: "detection/test.jsonl" },
        paddle_export: "detection/paddle/export.json",
      },
      e2e: { split_files: { train: "e2e/train.jsonl", val: "e2e/val.jsonl", test: "e2e/test.jsonl" } },
    };
    await atomicJson(reportPath, report);
    await atomicJson(statePath, { schema_version: 1, status: "completed", profile, report_sha256: await fileSha256(reportPath) });
    return report;
  } catch (error) {
    await atomicJson(statePath, { schema_version: 1, status: "failed", profile, error: error instanceof Error ? error.message : String(error) }).catch(() => {});
    throw error;
  } finally {
    await lock.close();
    await unlink(lockPath).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  }
}
