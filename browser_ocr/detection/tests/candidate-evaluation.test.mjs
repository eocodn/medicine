import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { acquireAdvisoryLock, releaseAdvisoryLock } from "../../advisory_lock.mts";
import { buildCandidateSafetyEvaluation, runCandidateSafetyEvaluation } from "../candidate_evaluation.mjs";

const SHA = (character) => character.repeat(64);

function digest(content) {
  return createHash("sha256").update(content).digest("hex");
}

function corpus() {
  const region = (id, x, group) => ({
    region_id: id,
    text: id,
    polygon: [[x, 10], [x + 30, 10], [x + 30, 30], [x, 30]],
    natural_text_polygon: [[x, 10], [x + 30, 10], [x + 30, 30], [x, 30]],
    critical: true,
    association_group: group,
    semantic_role: "product",
  });
  return {
    corpus_id: "candidate-eval-corpus",
    gates: {
      min_recall: 1,
      min_precision: 1,
      min_critical_box_recall: 1,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples: [
      { id: "train-doc", split: "train", regions: [region("train-r", 10, "train-row")] },
      { id: "val-doc", split: "val", regions: [region("val-r", 20, "val-row")] },
      { id: "test-doc", split: "test", regions: [region("test-r", 30, "test-row")] },
    ],
  };
}

function candidate() {
  return {
    schema_version: 1,
    status: "ok",
    profile: {
      corpus_id: "candidate-eval-corpus",
      corpus_manifest_sha256: SHA("a"),
      checkpoint_sha256: SHA("b"),
      training_result_sha256: SHA("c"),
    },
    benchmark_model_key: "PP-OCRv5_mobile_det_candidate_test",
    onnx_sha256: SHA("d"),
    promotion_status: "pending_project_safety_evaluation",
  };
}

function benchmarkResult(inputCorpus) {
  return {
    schema_version: 1,
    corpus_id: inputCorpus.corpus_id,
    model: "PP-OCRv5_mobile_det_candidate_test",
    detector_edge: 640,
    asset_sha256: SHA("d"),
    performance: { latency_ms: { mean: 10 }, incremental_peak_rss_bytes: 123 },
    predictions: {
      schema_version: 1,
      corpus_id: inputCorpus.corpus_id,
      samples: inputCorpus.samples.map((sample) => ({
        id: sample.id,
        predictions: sample.split === "train"
          ? []
          : sample.regions.map((region) => ({ polygon: region.polygon, score: 0.99 })),
      })),
    },
  };
}

function runtimeCorpus() {
  return {
    schema_version: 1,
    corpus_id: "candidate-runtime-corpus",
    synthetic_only: false,
    gates: {
      min_recall: 1,
      min_precision: 1,
      min_critical_box_recall: 1,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples: [{
      id: "runtime-doc",
      image: "runtime.jpg",
      image_sha256: SHA("1"),
      width: 100,
      height: 100,
      scenario_tags: ["candidate_runtime_fixture"],
      risk_tags: [],
      regions: [{
        region_id: "runtime-product",
        text: "검증약",
        polygon: [[10, 10], [70, 10], [70, 35], [10, 35]],
        critical: true,
        association_group: "runtime-row",
        semantic_role: "product",
      }],
    }],
  };
}

async function completedRuntimeCandidate(root) {
  const corpusPath = join(root, "corpus.json");
  const corpusRaw = `${JSON.stringify(runtimeCorpus())}\n`;
  await writeFile(corpusPath, corpusRaw);
  const corpusSha256 = digest(corpusRaw);

  const candidateDir = join(root, "candidate");
  await mkdir(candidateDir, { recursive: true });
  const onnx = Buffer.from("fixture-onnx");
  const inferenceConfig = Buffer.from("fixture-config");
  const modelKey = "fixture_candidate";
  const benchmarkManifestRaw = `${JSON.stringify({
    schema_version: 1,
    models: { [modelKey]: { onnx_sha256: digest(onnx) } },
  })}\n`;
  const candidate = {
    schema_version: 1,
    status: "ok",
    profile: {
      corpus_id: "candidate-runtime-corpus",
      corpus_manifest_sha256: corpusSha256,
      checkpoint_sha256: SHA("b"),
      training_result_sha256: SHA("c"),
    },
    benchmark_model_key: modelKey,
    onnx_sha256: digest(onnx),
    inference_config_sha256: digest(inferenceConfig),
    benchmark_manifest_sha256: digest(benchmarkManifestRaw),
    promotion_status: "pending_project_safety_evaluation",
  };
  const candidateRaw = `${JSON.stringify(candidate)}\n`;
  const candidateSha256 = digest(candidateRaw);
  const candidatePath = join(candidateDir, "candidate.json");
  await writeFile(candidatePath, candidateRaw);
  await writeFile(join(candidateDir, "inference.onnx"), onnx);
  await writeFile(join(candidateDir, "inference.yml"), inferenceConfig);
  await writeFile(join(candidateDir, "benchmark-models.json"), benchmarkManifestRaw);
  await writeFile(join(candidateDir, "candidate-state.json"), `${JSON.stringify({
    status: "completed",
    profile: candidate.profile,
    result_sha256: candidateSha256,
  })}\n`);
  return { candidatePath, candidateSha256, corpusPath, corpusSha256 };
}

test("candidate promotion uses held-out test quality, not train diagnostics", () => {
  const inputCorpus = corpus();
  const result = buildCandidateSafetyEvaluation({
    candidate: candidate(),
    candidateSha256: SHA("e"),
    corpus: inputCorpus,
    corpusSha256: SHA("a"),
    benchmark: benchmarkResult(inputCorpus),
  });
  assert.equal(result.synthetic_test.status, "pass");
  assert.equal(result.diagnostics.full.status, "fail");
  assert.equal(result.promotion_status, "synthetic_test_pass_pending_real_holdout_and_android");
  assert.deepEqual(result.synthetic_test.evaluation_scope, { split: "test" });
});

test("candidate evaluation rejects corpus or ONNX provenance drift", () => {
  const inputCorpus = corpus();
  assert.throws(() => buildCandidateSafetyEvaluation({
    candidate: candidate(),
    candidateSha256: SHA("e"),
    corpus: inputCorpus,
    corpusSha256: SHA("f"),
    benchmark: benchmarkResult(inputCorpus),
  }), /corpus manifest SHA-256/);

  const wrongBenchmark = benchmarkResult(inputCorpus);
  wrongBenchmark.asset_sha256 = SHA("f");
  assert.throws(() => buildCandidateSafetyEvaluation({
    candidate: candidate(),
    candidateSha256: SHA("e"),
    corpus: inputCorpus,
    corpusSha256: SHA("a"),
    benchmark: wrongBenchmark,
  }), /ONNX SHA-256/);
});

test("candidate evaluation ignores a stale lock file when reusing a completed result", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-candidate-eval-stale-lock-"));
  try {
    const fixture = await completedRuntimeCandidate(root);
    const outputDir = join(root, "output");
    await mkdir(outputDir, { recursive: true });
    const lockPath = join(outputDir, ".candidate-evaluation.lock");
    await writeFile(lockPath, "stale-candidate-owner\n");
    const fingerprint = digest(JSON.stringify({
      runner_version: 1,
      candidate_sha256: fixture.candidateSha256,
      corpus_sha256: fixture.corpusSha256,
      edge: 640,
      threads: 1,
      promotion_split: "test",
    }));
    await writeFile(join(outputDir, "safety-evaluation.json"), `${JSON.stringify({
      fingerprint,
      status: "fixture-completed",
    })}\n`);

    const result = await runCandidateSafetyEvaluation({
      candidatePath: fixture.candidatePath,
      corpusPath: fixture.corpusPath,
      outputDir,
    });
    assert.equal(result.status, "fixture-completed");
    assert.equal(await readFile(lockPath, "utf8"), "stale-candidate-owner\n");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("candidate evaluation refuses a live advisory-lock owner", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-candidate-eval-live-lock-"));
  try {
    const fixture = await completedRuntimeCandidate(root);
    const outputDir = join(root, "output");
    await mkdir(outputDir, { recursive: true });
    const lock = await acquireAdvisoryLock(join(outputDir, ".candidate-evaluation.lock"), {
      busyMessage: "fixture busy",
      label: "fixture candidate evaluation lock",
    });
    try {
      await assert.rejects(
        runCandidateSafetyEvaluation({
          candidatePath: fixture.candidatePath,
          corpusPath: fixture.corpusPath,
          outputDir,
        }),
        /already active/,
      );
    } finally {
      await releaseAdvisoryLock(lock);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
