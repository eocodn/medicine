import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { buildHistoricalExposureArtifact } from "../historical_exposure_builder.mjs";
import { drugFamilyKey, validateHistoricalDrugExposure } from "../drug_holdout.mjs";

test("historical exposure is derived only from the authoritative training split", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-historical-exposure-"));
  try {
    const samples = [
      { id: "train-product", text: "가나다정 10mg", semantic_tags: ["product", "strength"] },
      { id: "train-dose", text: "1정", semantic_tags: ["dose"] },
      { id: "test-product", text: "라마바정", semantic_tags: ["product"] },
    ];
    await writeFile(join(root, "samples.jsonl"), `${samples.map((sample) => JSON.stringify(sample)).join("\n")}\n`);
    await writeFile(join(root, "manifest.json"), `${JSON.stringify({
      schema_version: 1,
      dataset_id: "fixture-old-recognizer",
      task: "text_recognition",
      samples_file: "samples.jsonl",
      metadata: { generator_version: "5" },
    })}\n`);
    const split = {
      dataset_id: "fixture-old-recognizer",
      dataset_fingerprint: "c".repeat(64),
      counts: { train: 2, val: 0, test: 1 },
      splits: { train: ["train-product", "train-dose"], val: [], test: ["test-product"] },
    };
    await writeFile(join(root, "split.json"), `${JSON.stringify(split)}\n`);
    const output = join(root, "exposure.json");
    const result = await buildHistoricalExposureArtifact({
      manifestPath: join(root, "manifest.json"),
      splitPath: join(root, "split.json"),
      checkpointSha256: "b".repeat(64),
      outputPath: output,
    });
    assert.equal(result.product_name_count, 1);
    const exposure = validateHistoricalDrugExposure(JSON.parse(await readFile(output, "utf8")));
    assert.deepEqual(exposure.product_names, ["가나다정"]);
    assert.deepEqual(exposure.families, [drugFamilyKey("가나다정")]);
    assert.equal(exposure.product_names.includes("라마바정"), false);
    const repeated = await buildHistoricalExposureArtifact({
      manifestPath: join(root, "manifest.json"), splitPath: join(root, "split.json"), checkpointSha256: "b".repeat(64), outputPath: output,
    });
    assert.equal(repeated.output_sha256, result.output_sha256);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
