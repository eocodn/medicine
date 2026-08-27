import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { validateCorpus } from "./contract.mjs";
import { evaluateDetections } from "./evaluation.mjs";
import { acquireAdvisoryLock, releaseAdvisoryLock } from "../advisory_lock.mts";

const HERE = dirname(fileURLToPath(import.meta.url));
const SHA256 = /^[0-9a-f]{64}$/;
const RUNNER_VERSION = 1;

function digest(content) {
  return createHash("sha256").update(content).digest("hex");
}

function requireSha(value, label) {
  if (typeof value !== "string" || !SHA256.test(value)) throw new Error(`${label} must be lowercase SHA-256`);
  return value;
}

function verifyCandidateIdentity(candidate, candidateSha256, corpus, corpusSha256, benchmark) {
  if (!candidate || candidate.schema_version !== 1 || candidate.status !== "ok") {
    throw new Error("detector candidate must be a completed schema-v1 result");
  }
  if (candidate.promotion_status !== "pending_project_safety_evaluation") {
    throw new Error("detector candidate has invalid promotion status");
  }
  requireSha(candidateSha256, "candidate result SHA-256");
  const profile = candidate.profile;
  if (!profile || typeof profile !== "object" || Array.isArray(profile)) throw new Error("detector candidate profile is missing");
  if (profile.corpus_id !== corpus.corpus_id) throw new Error("detector candidate corpus id does not match evaluation corpus");
  if (profile.corpus_manifest_sha256 !== corpusSha256) {
    throw new Error("detector candidate corpus manifest SHA-256 does not match evaluation corpus");
  }
  requireSha(candidate.onnx_sha256, "detector candidate ONNX SHA-256");
  if (benchmark?.corpus_id !== corpus.corpus_id) throw new Error("detector benchmark corpus id does not match candidate corpus");
  if (benchmark?.model !== candidate.benchmark_model_key) throw new Error("detector benchmark model key does not match candidate");
  if (benchmark?.asset_sha256 !== candidate.onnx_sha256) throw new Error("detector benchmark ONNX SHA-256 does not match candidate");
  if (!benchmark?.predictions) throw new Error("detector benchmark result is missing predictions");
}

export function buildCandidateSafetyEvaluation({
  candidate,
  candidateSha256,
  corpus,
  corpusSha256,
  benchmark,
}) {
  verifyCandidateIdentity(candidate, candidateSha256, corpus, corpusSha256, benchmark);
  const full = evaluateDetections(corpus, benchmark.predictions);
  const syntheticTest = evaluateDetections(corpus, benchmark.predictions, { split: "test" });
  const diagnostics = { full };
  for (const split of ["train", "val"]) {
    if (corpus.samples.some((sample) => sample.split === split)) {
      diagnostics[split] = evaluateDetections(corpus, benchmark.predictions, { split });
    }
  }
  return {
    schema_version: 1,
    candidate_sha256: candidateSha256,
    candidate_onnx_sha256: candidate.onnx_sha256,
    checkpoint_sha256: candidate.profile.checkpoint_sha256,
    training_result_sha256: candidate.profile.training_result_sha256,
    corpus_id: corpus.corpus_id,
    corpus_manifest_sha256: corpusSha256,
    detector_edge: benchmark.detector_edge,
    performance: benchmark.performance,
    synthetic_test: syntheticTest,
    diagnostics,
    promotion_status: syntheticTest.status === "pass"
      ? "synthetic_test_pass_pending_real_holdout_and_android"
      : "rejected_by_synthetic_test_safety_gates",
  };
}

async function atomicWrite(path, value) {
  const temporary = `${path}.partial-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { flag: "w" });
  await rename(temporary, path);
}

async function jsonFile(path, label) {
  let parsed;
  try {
    parsed = JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    throw new Error(`could not read ${label} ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${label} must contain a JSON object`);
  return parsed;
}

async function shaFile(path) {
  return digest(await readFile(path));
}

async function verifyCandidateFiles(candidatePath, candidate, candidateSha256) {
  const root = dirname(candidatePath);
  const state = await jsonFile(join(root, "candidate-state.json"), "detector candidate state");
  if (state.status !== "completed" || JSON.stringify(state.profile) !== JSON.stringify(candidate.profile)) {
    throw new Error("detector candidate state does not match completed candidate profile");
  }
  if (state.result_sha256 !== candidateSha256) throw new Error("detector candidate state result SHA-256 mismatch");
  const checks = [
    ["inference.onnx", candidate.onnx_sha256, "candidate ONNX"],
    ["inference.yml", candidate.inference_config_sha256, "candidate inference config"],
    ["benchmark-models.json", candidate.benchmark_manifest_sha256, "candidate benchmark manifest"],
  ];
  for (const [name, expected, label] of checks) {
    requireSha(expected, `${label} SHA-256`);
    const actual = await shaFile(join(root, name));
    if (actual !== expected) throw new Error(`${label} SHA-256 mismatch`);
  }
  const manifest = await jsonFile(join(root, "benchmark-models.json"), "candidate benchmark manifest");
  const model = manifest.models?.[candidate.benchmark_model_key];
  if (!model || model.onnx_sha256 !== candidate.onnx_sha256) throw new Error("candidate benchmark manifest does not bind candidate ONNX");
}

function runBenchmarkWorker({ corpusPath, candidateDir, candidate, edge, threads, outputPath }) {
  return new Promise((resolvePromise, reject) => {
    const args = [
      join(HERE, "detector_benchmark.py"),
      "--corpus", corpusPath,
      "--model-manifest", join(candidateDir, "benchmark-models.json"),
      "--model-root", candidateDir,
      "--model", candidate.benchmark_model_key,
      "--edge", String(edge),
      "--threads", String(threads),
      "--output", outputPath,
    ];
    const child = spawn("python", args, {
      stdio: ["ignore", "ignore", "inherit"],
      env: { ...process.env, OMP_NUM_THREADS: "1", OPENBLAS_NUM_THREADS: "1", MKL_NUM_THREADS: "1" },
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`detector candidate benchmark worker exited ${code}`));
    });
  });
}

export async function runCandidateSafetyEvaluation({ candidatePath, corpusPath, outputDir, edge = 640, threads = 1 }) {
  if (!Number.isInteger(edge) || edge <= 0) throw new Error("detector edge must be a positive integer");
  if (!Number.isInteger(threads) || threads <= 0) throw new Error("threads must be a positive integer");
  const resolvedCandidate = resolve(candidatePath);
  const resolvedCorpus = resolve(corpusPath);
  const resolvedOutput = resolve(outputDir);
  const candidate = await jsonFile(resolvedCandidate, "detector candidate");
  const candidateSha256 = await shaFile(resolvedCandidate);
  await verifyCandidateFiles(resolvedCandidate, candidate, candidateSha256);
  const corpusBytes = await readFile(resolvedCorpus);
  const corpus = validateCorpus(JSON.parse(corpusBytes.toString("utf8")));
  const corpusSha256 = digest(corpusBytes);
  if (candidate.profile?.corpus_id !== corpus.corpus_id || candidate.profile?.corpus_manifest_sha256 !== corpusSha256) {
    throw new Error("detector candidate is not bound to the requested corpus manifest SHA-256");
  }

  const fingerprint = digest(JSON.stringify({
    runner_version: RUNNER_VERSION,
    candidate_sha256: candidateSha256,
    corpus_sha256: corpusSha256,
    edge,
    threads,
    promotion_split: "test",
  }));
  await mkdir(resolvedOutput, { recursive: true });
  const lockPath = join(resolvedOutput, ".candidate-evaluation.lock");
  const lock = await acquireAdvisoryLock(lockPath, {
    busyMessage: "detector candidate evaluation is already active for this output directory",
    label: "detector candidate evaluation lock",
  });
  try {
    const resultPath = join(resolvedOutput, "safety-evaluation.json");
    try {
      const existing = await jsonFile(resultPath, "detector candidate safety evaluation");
      if (existing.fingerprint !== fingerprint) throw new Error("completed detector candidate evaluation fingerprint mismatch");
      return existing;
    } catch (error) {
      if (!String(error?.message || error).includes("ENOENT")) throw error;
    }

    const benchmarkPath = join(resolvedOutput, "benchmark-result.json");
    await rm(benchmarkPath, { force: true });
    process.stderr.write(`[det-candidate-eval] benchmark ${candidate.benchmark_model_key}@${edge}\n`);
    await runBenchmarkWorker({
      corpusPath: resolvedCorpus,
      candidateDir: dirname(resolvedCandidate),
      candidate,
      edge,
      threads,
      outputPath: benchmarkPath,
    });
    const benchmark = await jsonFile(benchmarkPath, "detector candidate benchmark result");
    const evaluation = buildCandidateSafetyEvaluation({ candidate, candidateSha256, corpus, corpusSha256, benchmark });
    const result = {
      runner_version: RUNNER_VERSION,
      fingerprint,
      benchmark_result_sha256: await shaFile(benchmarkPath),
      ...evaluation,
    };
    await atomicWrite(resultPath, result);
    process.stderr.write(`[det-candidate-eval] synthetic-test ${evaluation.synthetic_test.status}\n`);
    return result;
  } finally {
    await releaseAdvisoryLock(lock);
  }
}

function option(args, name, fallback = null) {
  const index = args.lastIndexOf(name);
  if (index < 0) return fallback;
  if (index + 1 >= args.length) throw new Error(`${name} requires a value`);
  return args[index + 1];
}

function integerOption(args, name, fallback) {
  const value = Number(option(args, name, String(fallback)));
  if (!Number.isInteger(value)) throw new Error(`${name} must be an integer`);
  return value;
}

async function main(argv) {
  const candidatePath = option(argv, "--candidate");
  const corpusPath = option(argv, "--corpus");
  const outputDir = option(argv, "--output");
  if (!candidatePath || !corpusPath || !outputDir) {
    throw new Error("usage: candidate_evaluation.mjs --candidate FILE --corpus FILE --output DIR [--edge 640] [--threads 1] [--json]");
  }
  const result = await runCandidateSafetyEvaluation({
    candidatePath,
    corpusPath,
    outputDir,
    edge: integerOption(argv, "--edge", 640),
    threads: integerOption(argv, "--threads", 1),
  });
  process.stdout.write(argv.includes("--json") ? `${JSON.stringify(result)}\n` : `${JSON.stringify(result, null, 2)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 2;
  });
}