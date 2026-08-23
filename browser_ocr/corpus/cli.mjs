import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";

import { auditCoverage } from "../detection/coverage.mjs";
import { validateUnifiedCorpus } from "./contract.mjs";
import { observedDrugLeakageReport } from "./drug_holdout.mjs";
import { generateUnifiedCorpus } from "./generator.mjs";
import { buildHistoricalExposureArtifact } from "./historical_exposure_builder.mjs";
import { materializeUnifiedViews } from "./materialize.mjs";

function option(args, name, fallback = null) {
  const index = args.lastIndexOf(name);
  if (index < 0) return fallback;
  if (index + 1 >= args.length) throw new Error(`${name} requires a value`);
  return args[index + 1];
}

function integerOption(args, name, fallback) {
  const parsed = Number(option(args, name, String(fallback)));
  if (!Number.isInteger(parsed)) throw new Error(`${name} must be an integer`);
  return parsed;
}

function safePath(root, relativePath) {
  const absolute = resolve(root, relativePath);
  if (absolute !== root && !absolute.startsWith(`${root}${sep}`)) throw new Error(`path escapes corpus root: ${relativePath}`);
  return absolute;
}

async function loadCorpus(path) {
  return validateUnifiedCorpus(JSON.parse(await readFile(path, "utf8")));
}

async function validateFiles(corpus, corpusPath) {
  const root = dirname(resolve(corpusPath));
  for (const sample of corpus.samples) {
    const path = safePath(root, sample.image);
    const info = await stat(path);
    if (!info.isFile()) throw new Error(`${sample.id} image is not a file`);
    const digest = createHash("sha256").update(await readFile(path)).digest("hex");
    if (digest !== sample.image_sha256) throw new Error(`${sample.id} image SHA-256 mismatch`);
  }
}

function summary(corpus) {
  return {
    schema_version: corpus.schema_version,
    status: "valid",
    corpus_id: corpus.corpus_id,
    tasks: corpus.tasks,
    generator: corpus.generator,
    documents: corpus.samples.length,
    regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.length, 0),
    critical_regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.filter((region) => region.critical).length, 0),
    splits: Object.fromEntries(["train", "val", "test"].map((name) => [name, corpus.samples.filter((sample) => sample.split === name).length])),
    ...(corpus.drug_name_policy ? {
      drug_name_policy: corpus.drug_name_policy,
      drug_name_splits: Object.fromEntries(["train", "val", "test"].map((name) => [name, corpus.samples.filter((sample) => sample.drug_name_split === name).length])),
      drug_name_exposure: Object.fromEntries(["seen", "unseen"].map((name) => [name, corpus.samples.filter((sample) => sample.drug_name_exposure === name).length])),
    } : {}),
  };
}

async function main(argv) {
  const [command, ...args] = argv;
  const json = args.includes("--json");
  let result;
  if (command === "historical-exposure") {
    const manifestPath = option(args, "--manifest");
    const splitPath = option(args, "--split");
    const checkpointSha256 = option(args, "--checkpoint-sha256");
    const outputPath = option(args, "--output");
    if (!manifestPath || !splitPath || !checkpointSha256 || !outputPath) {
      throw new Error("historical-exposure requires --manifest FILE --split FILE --checkpoint-sha256 SHA256 --output FILE");
    }
    result = await buildHistoricalExposureArtifact({
      manifestPath: resolve(manifestPath),
      splitPath: resolve(splitPath),
      checkpointSha256,
      outputPath: resolve(outputPath),
    });
  } else if (command === "generate") {
    const outputDir = option(args, "--output");
    const canonicalDb = option(args, "--canonical-db");
    const drugSplitSeed = option(args, "--drug-split-seed");
    const historicalDrugExposure = option(args, "--historical-drug-exposure");
    if (!outputDir || !canonicalDb || drugSplitSeed === null || !historicalDrugExposure) {
      throw new Error("generate requires --output DIR --canonical-db FILE --drug-split-seed INTEGER --historical-drug-exposure FILE");
    }
    const parsedDrugSplitSeed = Number(drugSplitSeed);
    if (!Number.isInteger(parsedDrugSplitSeed)) throw new Error("--drug-split-seed must be an integer");
    const corpus = await generateUnifiedCorpus({
      outputDir: resolve(outputDir),
      count: integerOption(args, "--count", 36),
      seed: integerOption(args, "--seed", 153),
      drugSplitSeed: parsedDrugSplitSeed,
      historicalDrugExposure: resolve(historicalDrugExposure),
      canonicalDb: resolve(canonicalDb),
      renderConcurrency: integerOption(args, "--concurrency", 1),
    });
    if (args.includes("--materialize")) {
      const views = await materializeUnifiedViews({
        corpusPath: resolve(outputDir, "manifest.json"),
        outputDir: resolve(outputDir, "views"),
        python: option(args, "--python", "/opt/detection-venv/bin/python"),
      });
      result = { ...summary(corpus), views };
    } else {
      result = summary(corpus);
    }
  } else if (command === "validate") {
    const corpusPath = option(args, "--corpus");
    if (!corpusPath) throw new Error("validate requires --corpus FILE");
    const corpus = await loadCorpus(resolve(corpusPath));
    await validateFiles(corpus, resolve(corpusPath));
    result = summary(corpus);
  } else if (command === "materialize") {
    const corpusPath = option(args, "--corpus");
    const outputDir = option(args, "--output");
    if (!corpusPath || !outputDir) throw new Error("materialize requires --corpus FILE --output DIR");
    const corpus = await loadCorpus(resolve(corpusPath));
    if (corpus.schema_version !== 3) throw new Error("materialize requires unified OCR corpus schema v3");
    await validateFiles(corpus, resolve(corpusPath));
    result = await materializeUnifiedViews({
      corpusPath: resolve(corpusPath),
      outputDir: resolve(outputDir),
      python: option(args, "--python", "/opt/detection-venv/bin/python"),
    });
  } else if (command === "audit") {
    const corpusPath = option(args, "--corpus");
    if (!corpusPath) throw new Error("audit requires --corpus FILE");
    const corpus = await loadCorpus(resolve(corpusPath));
    await validateFiles(corpus, resolve(corpusPath));
    result = auditCoverage(corpus, {
      minimumPerLayout: integerOption(args, "--min-layout", 1),
      minimumPerCapture: integerOption(args, "--min-capture", 1),
      minimumPerRisk: integerOption(args, "--min-risk", 1),
      minimumCriticalPerRole: integerOption(args, "--min-critical-role", 1),
    });
    result.drug_names = corpus.drug_name_policy ? observedDrugLeakageReport(corpus.samples) : null;
    result.corpus_id = corpus.corpus_id;
    result.tasks = corpus.tasks;
    result.splits = summary(corpus).splits;
    if (result.status !== "pass" || result.drug_names?.status === "fail") process.exitCode = 1;
  } else {
    throw new Error("usage: cli.mjs <historical-exposure|generate|validate|materialize|audit> [options] [--json]");
  }
  process.stdout.write(json ? `${JSON.stringify(result)}\n` : `${JSON.stringify(result, null, 2)}\n`);
}

main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 2;
});
