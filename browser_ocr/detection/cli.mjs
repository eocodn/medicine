import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, resolve, sep } from "node:path";

import { auditCoverage } from "./coverage.mjs";
import { runDetectorBenchmarkMatrix } from "./benchmark.mjs";
import { validateCorpus } from "./contract.mjs";
import { benchmarkMatrix, loadDetectorModelManifest } from "./detector_models.mjs";
import { evaluateDetections } from "./evaluation.mjs";
import { fetchDetectorAssets } from "./fetch_detector_assets.mjs";
import { generateSyntheticCorpus } from "./synthetic.mjs";

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
  return validateCorpus(JSON.parse(await readFile(path, "utf8")));
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

async function main(argv) {
  const [command, ...args] = argv;
  const json = args.includes("--json");
  let result;
  if (command === "generate") {
    const outputDir = option(args, "--output");
    const canonicalDb = option(args, "--canonical-db");
    const drugSplitSeed = option(args, "--drug-split-seed");
    const historicalDrugExposure = option(args, "--historical-drug-exposure");
    if (!outputDir || !canonicalDb || drugSplitSeed === null || !historicalDrugExposure) {
      throw new Error("generate requires --output DIR --canonical-db FILE --drug-split-seed INTEGER --historical-drug-exposure FILE");
    }
    const parsedDrugSplitSeed = Number(drugSplitSeed);
    if (!Number.isInteger(parsedDrugSplitSeed)) throw new Error("--drug-split-seed must be an integer");
    result = await generateSyntheticCorpus({
      outputDir: resolve(outputDir),
      count: integerOption(args, "--count", 36),
      seed: integerOption(args, "--seed", 153),
      drugSplitSeed: parsedDrugSplitSeed,
      historicalDrugExposure: resolve(historicalDrugExposure),
      canonicalDb: resolve(canonicalDb),
    });
  } else if (command === "validate") {
    const corpusPath = option(args, "--corpus");
    if (!corpusPath) throw new Error("validate requires --corpus FILE");
    const corpus = await loadCorpus(resolve(corpusPath));
    await validateFiles(corpus, resolve(corpusPath));
    result = {
      schema_version: corpus.schema_version,
      status: "valid",
      corpus_id: corpus.corpus_id,
      generator: corpus.generator,
      samples: corpus.samples.length,
      regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.length, 0),
      critical_regions: corpus.samples.reduce((sum, sample) => sum + sample.regions.filter((region) => region.critical).length, 0),
    };
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
    if (result.status !== "pass") process.exitCode = 1;
  } else if (command === "evaluate") {
    const corpusPath = option(args, "--corpus");
    const predictionsPath = option(args, "--predictions");
    if (!corpusPath || !predictionsPath) throw new Error("evaluate requires --corpus FILE --predictions FILE");
    const corpus = await loadCorpus(resolve(corpusPath));
    await validateFiles(corpus, resolve(corpusPath));
    const predictions = JSON.parse(await readFile(resolve(predictionsPath), "utf8"));
    result = evaluateDetections(corpus, predictions);
    if (result.status !== "pass") process.exitCode = 1;
  } else if (command === "matrix") {
    result = benchmarkMatrix(await loadDetectorModelManifest());
  } else if (command === "assets") {
    const outputDir = option(args, "--output", "browser_ocr/detection/.cache/models");
    result = await fetchDetectorAssets({ outputDir: resolve(outputDir) });
  } else if (command === "benchmark") {
    const corpusPath = option(args, "--corpus", "browser_ocr/detection/corpus/manifest.json");
    const cacheDir = option(args, "--cache", "browser_ocr/detection/.cache/models");
    const outputDir = option(args, "--output", "browser_ocr/detection/results/zero-shot");
    const model = option(args, "--model");
    const edge = option(args, "--edge");
    result = await runDetectorBenchmarkMatrix({
      corpusPath: resolve(corpusPath),
      cacheDir: resolve(cacheDir),
      outputDir: resolve(outputDir),
      models: model ? [model] : null,
      detectorEdges: edge ? [integerOption(args, "--edge", 0)] : null,
      threads: integerOption(args, "--threads", 1),
    });
  } else {
    throw new Error("usage: cli.mjs <generate|validate|audit|evaluate|matrix|assets|benchmark> [options] [--json]");
  }
  process.stdout.write(json ? `${JSON.stringify(result)}\n` : `${JSON.stringify(result, null, 2)}\n`);
}

main(process.argv.slice(2)).catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 2;
});
