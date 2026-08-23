import assert from "node:assert/strict";
import test from "node:test";

import { buildCandidateSafetyEvaluation } from "../candidate_evaluation.mjs";

const SHA = (character) => character.repeat(64);

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