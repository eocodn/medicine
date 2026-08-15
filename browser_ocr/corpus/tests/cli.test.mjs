import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";

import { createCanonicalDrugDb } from "./fixtures.mjs";

function run(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["browser_ocr/corpus/cli.mjs", ...args], { cwd: process.cwd() });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

test("ocr corpus CLI generates and validates the canonical multi-stage corpus", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-ocr-corpus-cli-"));
  try {
    const canonicalDb = join(root, "canonical.sqlite");
    await createCanonicalDrugDb(canonicalDb);
    const generated = await run([
      "generate", "--output", root, "--canonical-db", canonicalDb,
      "--count", "6", "--seed", "501", "--json",
    ]);
    assert.equal(generated.code, 0, generated.stderr);
    const generatedJson = JSON.parse(generated.stdout);
    assert.equal(generatedJson.schema_version, 3);
    assert.deepEqual(generatedJson.tasks, ["detection", "recognition", "parsing", "e2e"]);
    assert.equal(generatedJson.documents, 6);
    assert.equal(generatedJson.generator.version, 5);
    assert.equal(generatedJson.drug_name_policy.source.dataset_key, "mfds_permit:products");

    const validated = await run(["validate", "--corpus", join(root, "manifest.json"), "--json"]);
    assert.equal(validated.code, 0, validated.stderr);
    const validatedJson = JSON.parse(validated.stdout);
    assert.equal(validatedJson.corpus_id, generatedJson.corpus_id);
    assert.equal(validatedJson.regions, generatedJson.regions);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
