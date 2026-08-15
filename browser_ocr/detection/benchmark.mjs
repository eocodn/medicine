import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, open, readFile, readdir, rename, rm, unlink, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { validateCorpus } from "./contract.mjs";
import { loadDetectorModelManifest, benchmarkMatrix } from "./detector_models.mjs";
import { evaluateDetections } from "./evaluation.mjs";
import { fetchDetectorAssets } from "./fetch_detector_assets.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const RUNNER_VERSION = 1;

function digest(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function atomicWrite(path, value) {
  const temporary = `${path}.partial-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`);
  await rename(temporary, path);
}

async function maybeJson(path) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

function runPython(args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn("python", [join(HERE, "detector_benchmark.py"), ...args], {
      stdio: ["ignore", "ignore", "inherit"],
      env: { ...process.env, OMP_NUM_THREADS: "1", OPENBLAS_NUM_THREADS: "1", MKL_NUM_THREADS: "1" },
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`detector benchmark worker exited ${code}`));
    });
  });
}

function runKey(model, detectorEdge) {
  return `${model}@${detectorEdge}`;
}

function runFilename(model, detectorEdge) {
  return `${model.replaceAll(/[^A-Za-z0-9._-]/g, "_")}-${detectorEdge}.json`;
}

function rankingScore(run) {
  return [
    -run.quality.critical_box_recall,
    run.quality.cross_association_merges,
    run.quality.merge_errors,
    -run.quality.recall,
    run.performance.latency_ms.mean,
    run.model_bytes,
  ];
}

function compareRuns(left, right) {
  const a = rankingScore(left);
  const b = rankingScore(right);
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index];
  }
  return left.key.localeCompare(right.key);
}

async function loadCorpus(corpusPath) {
  return validateCorpus(JSON.parse(await readFile(corpusPath, "utf8")));
}

async function acquireBenchmarkLock(path) {
  try {
    return await open(path, "wx");
  } catch (error) {
    if (error?.code === "EEXIST") throw new Error("detector benchmark is already running for this output directory");
    throw error;
  }
}

async function verifyCompletedRuns(outputDir, state) {
  const keys = new Set();
  for (const run of state.runs || []) {
    if (!run?.key || !run.result_file) throw new Error("invalid benchmark checkpoint run metadata");
    if (keys.has(run.key)) throw new Error(`duplicate benchmark checkpoint run ${run.key}`);
    keys.add(run.key);
    const raw = await maybeJson(join(outputDir, run.result_file));
    if (!raw) throw new Error(`benchmark checkpoint result is missing: ${run.key}`);
    if (runKey(raw.model, raw.detector_edge) !== run.key || raw.asset_sha256 !== run.asset_sha256) {
      throw new Error(`benchmark checkpoint result mismatch: ${run.key}`);
    }
  }
  if (!Array.isArray(state.completed) || state.completed.length !== keys.size
    || state.completed.some((key) => !keys.has(key))) {
    throw new Error("invalid benchmark checkpoint completed set");
  }
}

export async function runDetectorBenchmarkMatrix({
  corpusPath,
  cacheDir,
  outputDir,
  models = null,
  detectorEdges = null,
  threads = 1,
}) {
  if (!Number.isInteger(threads) || threads <= 0) throw new Error("threads must be a positive integer");
  const resolvedCorpus = resolve(corpusPath);
  const resolvedCache = resolve(cacheDir);
  const resolvedOutput = resolve(outputDir);
  const corpus = await loadCorpus(resolvedCorpus);
  const detectorManifest = await loadDetectorModelManifest();
  const canonicalMatrix = benchmarkMatrix(detectorManifest);
  const selectedModels = models || canonicalMatrix.models;
  const selectedEdges = detectorEdges || canonicalMatrix.detector_edges;
  for (const model of selectedModels) if (!detectorManifest.models[model]) throw new Error(`unknown detector model ${model}`);
  for (const edge of selectedEdges) if (!canonicalMatrix.detector_edges.includes(edge)) throw new Error(`unsupported detector edge ${edge}`);

  await mkdir(join(resolvedOutput, "runs"), { recursive: true });
  const benchmarkLockPath = join(resolvedOutput, ".benchmark.lock");
  const benchmarkLock = await acquireBenchmarkLock(benchmarkLockPath);
  try {
  const assets = await fetchDetectorAssets({ outputDir: resolvedCache, modelNames: selectedModels });
  const assetByName = new Map(assets.models.map((asset) => [asset.model, asset]));
  const corpusBytes = await readFile(resolvedCorpus);
  const fingerprint = digest(JSON.stringify({
    runner_version: RUNNER_VERSION,
    corpus_sha256: digest(corpusBytes),
    models: selectedModels.map((name) => ({ name, sha256: detectorManifest.models[name].sha256 })),
    detector_edges: selectedEdges,
    threads,
  }));
  const statePath = join(resolvedOutput, ".benchmark-state.json");
  const summaryPath = join(resolvedOutput, "summary.json");
  const completedSummary = await maybeJson(summaryPath);
  if (completedSummary) {
    if (completedSummary.fingerprint !== fingerprint) throw new Error("benchmark configuration mismatch with completed summary");
    return completedSummary;
  }

  let state = await maybeJson(statePath);
  if (state) {
    if (state.fingerprint !== fingerprint) throw new Error("benchmark configuration mismatch with checkpoint");
    await verifyCompletedRuns(resolvedOutput, state);
  } else {
    const existing = (await readdir(join(resolvedOutput, "runs"))).filter((name) => name.endsWith(".json"));
    if (existing.length) throw new Error("benchmark run directory is non-empty without authoritative state");
    state = { schema_version: 1, fingerprint, completed: [], runs: [] };
    await atomicWrite(statePath, state);
  }

  const targetRuns = selectedModels.flatMap((model) => selectedEdges.map((detectorEdge) => ({ model, detectorEdge })));
  for (const target of targetRuns) {
    const key = runKey(target.model, target.detectorEdge);
    if (state.completed.includes(key)) continue;
    const asset = assetByName.get(target.model);
    if (!asset) throw new Error(`missing fetched asset ${target.model}`);
    const resultPath = join(resolvedOutput, "runs", runFilename(target.model, target.detectorEdge));
    process.stderr.write(`[det-matrix] start ${key}\n`);
    await runPython([
      "--corpus", resolvedCorpus,
      "--model-manifest", join(HERE, "detector-models.json"),
      "--model-root", resolvedCache,
      "--model", target.model,
      "--edge", String(target.detectorEdge),
      "--threads", String(threads),
      "--output", resultPath,
    ]);
    const raw = await maybeJson(resultPath);
    if (!raw || raw.model !== target.model || raw.detector_edge !== target.detectorEdge || raw.asset_sha256 !== detectorManifest.models[target.model].sha256) {
      throw new Error(`invalid benchmark worker result for ${key}`);
    }
    const evaluation = evaluateDetections(corpus, raw.predictions);
    const run = {
      key,
      model: target.model,
      detector_edge: target.detectorEdge,
      asset_sha256: raw.asset_sha256,
      model_bytes: raw.model_bytes,
      postprocess: raw.postprocess,
      performance: raw.performance,
      quality_status: evaluation.status,
      quality: evaluation.metrics,
      result_file: join("runs", basename(resultPath)),
    };
    state.completed.push(key);
    state.runs.push(run);
    await atomicWrite(statePath, state);
    process.stderr.write(`[det-matrix] done ${key} critical=${run.quality.critical_box_recall.toFixed(4)} recall=${run.quality.recall.toFixed(4)} latency=${run.performance.latency_ms.mean.toFixed(1)}ms\n`);
  }

  const ranked = state.runs.slice().sort(compareRuns).map((run, index) => ({ rank: index + 1, ...run }));
  const summary = {
    schema_version: 1,
    fingerprint,
    corpus_id: corpus.corpus_id,
    corpus_samples: corpus.samples.length,
    runner_version: RUNNER_VERSION,
    runtime_scope: "development_cpu_proxy_not_android_release_gate",
    threads,
    models: selectedModels,
    detector_edges: selectedEdges,
    status: "complete",
    ranked_runs: ranked,
  };
  await atomicWrite(summaryPath, summary);
  await rm(statePath, { force: true });
  return summary;
  } finally {
    await benchmarkLock.close();
    await unlink(benchmarkLockPath).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}