import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { validateCorpus } from "../contract.mjs";
import { evaluateDetections } from "../evaluation.mjs";
import { generateSyntheticCorpus } from "../synthetic.mjs";

function tinyCorpus() {
  return {
    schema_version: 1,
    corpus_id: "tiny",
    synthetic_only: true,
    gates: {
      min_recall: 1,
      min_precision: 1,
      min_critical_box_recall: 1,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples: [{
      id: "sample",
      image: "sample.svg",
      image_sha256: "a".repeat(64),
      width: 200,
      height: 100,
      scenario_tags: ["prescription_table"],
      risk_tags: ["row_association"],
      regions: [
        { region_id: "a", text: "약A", polygon: [[10, 10], [80, 10], [80, 35], [10, 35]], critical: true, association_group: "row-a", semantic_role: "product" },
        { region_id: "b", text: "1정", polygon: [[100, 10], [150, 10], [150, 35], [100, 35]], critical: true, association_group: "row-a", semantic_role: "dose" },
        { region_id: "c", text: "약B", polygon: [[10, 55], [80, 55], [80, 80], [10, 80]], critical: true, association_group: "row-b", semantic_role: "product" },
      ],
    }],
  };
}

test("contract validates strict full-document quadrilateral corpus", () => {
  const corpus = validateCorpus(tinyCorpus());
  assert.equal(corpus.samples[0].regions.length, 3);
  const broken = tinyCorpus();
  broken.samples[0].regions[0].polygon[0] = [-1, 10];
  assert.throws(() => validateCorpus(broken), /outside image bounds/);
});

test("synthetic generator is deterministic and emits valid full documents", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-"));
  try {
    const first = join(root, "a");
    const second = join(root, "b");
    const a = await generateSyntheticCorpus({ outputDir: first, count: 6, seed: 153 });
    const b = await generateSyntheticCorpus({ outputDir: second, count: 6, seed: 153 });
    assert.deepEqual(a, b);
    assert.equal(a.samples.length, 6);
    assert.ok(a.samples.every((sample) => sample.width === 1280 && sample.height === 1600));
    assert.ok(a.samples.some((sample) => sample.scenario_tags.includes("multi_medication")));
    const svg = await readFile(join(first, a.samples[0].image), "utf8");
    assert.match(svg, /<svg/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("evaluation distinguishes perfect detections, merges, and critical misses", () => {
  const corpus = validateCorpus(tinyCorpus());
  const perfect = {
    schema_version: 1,
    corpus_id: "tiny",
    samples: [{ id: "sample", predictions: corpus.samples[0].regions.map((region) => ({ polygon: region.polygon, score: 0.99 })) }],
  };
  const good = evaluateDetections(corpus, perfect);
  assert.equal(good.status, "pass");
  assert.equal(good.metrics.critical_box_recall, 1);
  assert.equal(good.metrics.merge_errors, 0);

  const merged = structuredClone(perfect);
  merged.samples[0].predictions = [
    { polygon: [[5, 5], [155, 5], [155, 85], [5, 85]], score: 0.99 },
  ];
  const badMerge = evaluateDetections(corpus, merged);
  assert.equal(badMerge.status, "fail");
  assert.ok(badMerge.metrics.merge_errors >= 1);
  assert.ok(badMerge.metrics.cross_association_merges >= 1);

  const missed = structuredClone(perfect);
  missed.samples[0].predictions.pop();
  const badRecall = evaluateDetections(corpus, missed);
  assert.equal(badRecall.status, "fail");
  assert.ok(badRecall.metrics.critical_box_recall < 1);
});