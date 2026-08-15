import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { auditCoverage } from "../coverage.mjs";
import { validateCorpus } from "../contract.mjs";
import { CAPTURE_PROFILES, LAYOUT_FAMILIES } from "../synthetic_catalog.mjs";
import { generateSyntheticCorpus } from "../synthetic.mjs";

test("scaled generator covers layout and capture families with transformed GT", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-v2-"));
  try {
    const corpus = await generateSyntheticCorpus({ outputDir: root, count: 16, seed: 153 });
    assert.equal(corpus.schema_version, 2);
    assert.equal(corpus.generator.version, 2);
    assert.equal(corpus.samples.length, 16);
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.layout_family)), new Set(LAYOUT_FAMILIES));
    assert.deepEqual(new Set(corpus.samples.map((sample) => sample.capture_profile)), new Set(CAPTURE_PROFILES));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("glare")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("blur")));
    assert.ok(corpus.samples.some((sample) => sample.risk_tags.includes("oblique_geometry")));
    assert.ok(corpus.samples.some((sample) => sample.regions.some((region) => region.region_class === "distractor")));
    const transformed = corpus.samples.find((sample) => sample.capture_profile !== "flat_clean");
    assert.ok(transformed);
    assert.ok(transformed.regions.some((region) => JSON.stringify(region.polygon) !== JSON.stringify(region.source_polygon)));
    validateCorpus(corpus);
    const svg = await readFile(join(root, transformed.image), "utf8");
    assert.match(svg, /capture-/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("generation resumes exactly after interruption and rejects config drift", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-resume-"));
  let completed = 0;
  try {
    await assert.rejects(
      generateSyntheticCorpus({
        outputDir: root,
        count: 12,
        seed: 901,
        onProgress(event) {
          completed = event.completed;
          if (event.completed === 4) throw new Error("intentional-stop");
        },
      }),
      /intentional-stop/,
    );
    assert.equal(completed, 4);
    const state = JSON.parse(await readFile(join(root, ".generation-state.json"), "utf8"));
    assert.equal(state.completed, 4);

    await assert.rejects(
      generateSyntheticCorpus({ outputDir: root, count: 12, seed: 902 }),
      /generation configuration mismatch/,
    );

    const resumed = await generateSyntheticCorpus({ outputDir: root, count: 12, seed: 901 });
    const freshRoot = join(root, "fresh");
    const fresh = await generateSyntheticCorpus({ outputDir: freshRoot, count: 12, seed: 901 });
    assert.deepEqual(resumed, fresh);
    await assert.rejects(readFile(join(root, ".generation-state.json"), "utf8"), /ENOENT/);

    await writeFile(join(root, resumed.samples[0].image), "corrupted");
    await assert.rejects(
      generateSyntheticCorpus({ outputDir: root, count: 12, seed: 901 }),
      /image SHA-256 mismatch/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("generation refuses orphaned output without authoritative state", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-orphan-"));
  try {
    await mkdir(join(root, "images"), { recursive: true });
    await writeFile(join(root, "images/orphan.svg"), "<svg/>");
    await assert.rejects(
      generateSyntheticCorpus({ outputDir: root, count: 16, seed: 153 }),
      /non-empty without a generation checkpoint/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("coverage audit fails closed when a required synthetic stratum disappears", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-det-coverage-"));
  try {
    const corpus = await generateSyntheticCorpus({ outputDir: root, count: 16, seed: 153 });
    const report = auditCoverage(corpus);
    assert.equal(report.status, "pass");
    assert.equal(report.layout_families.length, LAYOUT_FAMILIES.length);
    assert.equal(report.capture_profiles.length, CAPTURE_PROFILES.length);
    assert.ok(report.critical_semantic_roles.product > 0);
    assert.ok(report.critical_semantic_roles.dose > 0);

    const broken = structuredClone(corpus);
    for (const sample of broken.samples) {
      if (sample.capture_profile === "glare_shadow") sample.capture_profile = "flat_clean";
    }
    const failed = auditCoverage(broken);
    assert.equal(failed.status, "fail");
    assert.ok(failed.failures.some((failure) => failure.includes("glare_shadow")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});