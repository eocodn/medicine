import { createHash } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, rm, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { validateCorpus } from "./contract.mjs";
import { appearanceForIndex, printerDescriptor, renderMaterialOverlay, renderPrinterOverlay } from "./synthetic_appearance.mjs";
import { captureForIndex, transformPolygon } from "./synthetic_capture.mjs";
import {
  BACKGROUND_PROFILES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
} from "./synthetic_catalog.mjs";
import { buildLayout, DOCUMENT_HEIGHT, DOCUMENT_WIDTH, renderLayoutRegions } from "./synthetic_layouts.mjs";
import { rasterizerIdentity, renderRasterJpeg } from "./synthetic_raster.mjs";

const GENERATOR_ID = "medicine_full_document_synthetic";
const GENERATOR_VERSION = 2;
const GENERATOR_REVISION = 4;
const STATE_FILE = ".generation-state.json";
const LOCK_FILE = ".generation.lock";

function digest(content) {
  return createHash("sha256").update(content).digest("hex");
}

function rng(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

function sampleSeed(seed, index) {
  return (seed ^ Math.imul(index + 1, 0x9e3779b1)) >>> 0;
}

async function configurationFingerprint({ seed, count }) {
  const rasterizer = await rasterizerIdentity();
  return {
    rasterizer,
    fingerprint: digest(JSON.stringify({
      generator_id: GENERATOR_ID,
      generator_version: GENERATOR_VERSION,
      generator_revision: GENERATOR_REVISION,
      seed,
      count,
      width: DOCUMENT_WIDTH,
      height: DOCUMENT_HEIGHT,
      layout_families: LAYOUT_FAMILIES,
      capture_profiles: CAPTURE_PROFILES,
      material_profiles: MATERIAL_PROFILES,
      printer_profiles: PRINTER_PROFILES,
      background_profiles: BACKGROUND_PROFILES,
      rasterizer_fingerprint: rasterizer.fingerprint,
    })),
  };
}

async function atomicWrite(path, content) {
  const temporary = `${path}.tmp-${process.pid}`;
  await writeFile(temporary, content);
  await rename(temporary, path);
}

async function fileDigest(path) {
  return digest(await readFile(path));
}

function renderSourceSvg(layout, appearance) {
  const regions = renderLayoutRegions(layout.regions, printerDescriptor(appearance.printer_profile));
  const materialOverlay = renderMaterialOverlay(appearance.material_profile, DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  const printerOverlay = renderPrinterOverlay(appearance.printer_profile, DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${DOCUMENT_WIDTH}" height="${DOCUMENT_HEIGHT}" viewBox="0 0 ${DOCUMENT_WIDTH} ${DOCUMENT_HEIGHT}">
${layout.decorations}
${regions}
${printerOverlay}
${materialOverlay}
</svg>\n`;
}

function buildSamplePlan(index, seed) {
  const random = rng(sampleSeed(seed, index));
  const layout = buildLayout(index, random);
  const captureIndex = Math.floor(index / LAYOUT_FAMILIES.length) % CAPTURE_PROFILES.length;
  const capture = captureForIndex(captureIndex, random, DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  const appearance = appearanceForIndex(index);
  const id = `synthetic-${String(index + 1).padStart(6, "0")}`;
  const image = `images/${id}.jpg`;
  const regions = layout.regions.map((item) => ({
    ...item,
    source_polygon: item.polygon.map((point) => [...point]),
    polygon: transformPolygon(capture.homography, item.polygon),
  }));
  return {
    sample: {
      id,
      image,
      width: DOCUMENT_WIDTH,
      height: DOCUMENT_HEIGHT,
      sample_index: index,
      layout_family: layout.layout_family,
      capture_profile: capture.profile,
      material_profile: appearance.material_profile,
      printer_profile: appearance.printer_profile,
      background_profile: appearance.background_profile,
      capture: structuredClone(capture),
      scenario_tags: [...new Set(layout.scenario_tags)],
      risk_tags: [...new Set([...layout.risk_tags, ...capture.risk_tags, ...appearance.risk_tags])],
      regions,
    },
    sourceSvg: renderSourceSvg(layout, appearance),
    appearance,
  };
}

function comparableSample(sample) {
  const copy = structuredClone(sample);
  delete copy.image_sha256;
  return copy;
}

async function renderSample(index, seed, outputDir) {
  const plan = buildSamplePlan(index, seed);
  const outputPath = join(outputDir, plan.sample.image);
  const temporaryOutput = await renderRasterJpeg({
    sourceSvg: plan.sourceSvg,
    outputPath,
    capture: plan.sample.capture,
    appearance: plan.appearance,
    width: DOCUMENT_WIDTH,
    height: DOCUMENT_HEIGHT,
  });
  await rename(temporaryOutput, outputPath);
  plan.sample.image_sha256 = await fileDigest(outputPath);
  return plan.sample;
}

function buildCorpus({ seed, count, fingerprint, rasterizer, samples }) {
  return validateCorpus({
    schema_version: 2,
    corpus_id: `synthetic-prescription-detection-v2-seed-${seed}-n-${count}`,
    synthetic_only: true,
    generator: {
      id: GENERATOR_ID,
      version: GENERATOR_VERSION,
      revision: GENERATOR_REVISION,
      seed,
      count,
      fingerprint,
      rasterizer,
    },
    provenance: {
      kind: "procedural_synthetic",
      patient_data: false,
      reference_policy: "public-layout-reference-only",
      image_kind: "camera_like_raster",
    },
    gates: {
      min_recall: 0.95,
      min_precision: 0.9,
      min_critical_box_recall: 0.98,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples,
  });
}

function stateFor({ seed, count, fingerprint, rasterizer, samples = [] }) {
  return {
    schema_version: 2,
    generator_id: GENERATOR_ID,
    generator_version: GENERATOR_VERSION,
    generator_revision: GENERATOR_REVISION,
    generation_fingerprint: fingerprint,
    rasterizer_fingerprint: rasterizer.fingerprint,
    seed,
    count,
    completed: samples.length,
    samples,
  };
}

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function maybeReadJson(path) {
  try {
    return await readJson(path);
  } catch (error) {
    if (error && error.code === "ENOENT") return null;
    throw error;
  }
}

function assertMatchingState(state, { seed, count, fingerprint, rasterizer }) {
  if (state.generator_id !== GENERATOR_ID
    || state.generator_version !== GENERATOR_VERSION
    || state.generator_revision !== GENERATOR_REVISION
    || state.generation_fingerprint !== fingerprint
    || state.rasterizer_fingerprint !== rasterizer.fingerprint
    || state.seed !== seed
    || state.count !== count) {
    throw new Error("generation configuration mismatch with resumable state");
  }
  if (!Number.isInteger(state.completed) || state.completed < 0 || state.completed > count) {
    throw new Error("invalid generation checkpoint completed count");
  }
  if (!Array.isArray(state.samples) || state.samples.length !== state.completed) {
    throw new Error("invalid generation checkpoint sample count");
  }
}

async function verifyCheckpointImages(outputDir, samples) {
  for (const sample of samples) {
    const actual = await fileDigest(join(outputDir, sample.image));
    if (actual !== sample.image_sha256) throw new Error(`checkpoint image SHA-256 mismatch: ${sample.id}`);
  }
}

async function verifyCheckpointState(outputDir, state, seed) {
  for (const [index, sample] of state.samples.entries()) {
    const expected = buildSamplePlan(index, seed).sample;
    if (JSON.stringify(comparableSample(sample)) !== JSON.stringify(expected)) {
      throw new Error(`checkpoint sample metadata mismatch: ${sample.id || index}`);
    }
  }
  await verifyCheckpointImages(outputDir, state.samples);
}

function shouldReport(completed, count) {
  const interval = Math.max(1, Math.floor(count / 20));
  return completed === count || completed === 1 || completed % interval === 0;
}

async function acquireLock(path) {
  try {
    return await open(path, "wx");
  } catch (error) {
    if (error && error.code === "EEXIST") throw new Error("synthetic generation is already running for this output directory");
    throw error;
  }
}

export async function generateSyntheticCorpus({ outputDir, count = 36, seed = 153, onProgress = null }) {
  if (!Number.isInteger(count) || count <= 0) throw new Error("count must be a positive integer");
  if (!Number.isInteger(seed)) throw new Error("seed must be an integer");
  await mkdir(join(outputDir, "images"), { recursive: true });

  const { fingerprint, rasterizer } = await configurationFingerprint({ seed, count });
  const manifestPath = join(outputDir, "manifest.json");
  const statePath = join(outputDir, STATE_FILE);
  const lockPath = join(outputDir, LOCK_FILE);
  const lock = await acquireLock(lockPath);
  try {
    const existingManifest = await maybeReadJson(manifestPath);
    if (existingManifest) {
      if (existingManifest.generator?.fingerprint !== fingerprint) {
        throw new Error("generation configuration mismatch with completed corpus");
      }
      const corpus = validateCorpus(existingManifest);
      await verifyCheckpointImages(outputDir, corpus.samples);
      return corpus;
    }

    let state = await maybeReadJson(statePath);
    if (state) {
      assertMatchingState(state, { seed, count, fingerprint, rasterizer });
      await verifyCheckpointState(outputDir, state, seed);
    } else {
      const existingImages = await readdir(join(outputDir, "images"));
      if (existingImages.length) throw new Error("output images directory is non-empty without a generation checkpoint");
      state = stateFor({ seed, count, fingerprint, rasterizer });
      await atomicWrite(statePath, `${JSON.stringify(state, null, 2)}\n`);
    }

    for (let index = state.completed; index < count; index += 1) {
      const sample = await renderSample(index, seed, outputDir);
      state.samples.push(sample);
      state.completed = state.samples.length;
      await atomicWrite(statePath, `${JSON.stringify(state, null, 2)}\n`);

      const event = {
        completed: state.completed,
        total: count,
        sample_id: sample.id,
        layout_family: sample.layout_family,
        capture_profile: sample.capture_profile,
        material_profile: sample.material_profile,
      };
      if (shouldReport(state.completed, count)) {
        const percent = Math.round(state.completed * 100 / count);
        process.stderr.write(`[det-synth] ${state.completed}/${count} ${percent}% ${sample.layout_family}/${sample.capture_profile}/${sample.material_profile}\n`);
      }
      if (onProgress) onProgress(event);
    }

    const corpus = buildCorpus({ seed, count, fingerprint, rasterizer, samples: state.samples });
    await atomicWrite(manifestPath, `${JSON.stringify(corpus, null, 2)}\n`);
    await rm(statePath, { force: true });
    return corpus;
  } finally {
    await lock.close();
    await unlink(lockPath).catch((error) => {
      if (!error || error.code !== "ENOENT") throw error;
    });
  }
}

export const syntheticDimensions = { width: DOCUMENT_WIDTH, height: DOCUMENT_HEIGHT };
export const syntheticGenerator = { id: GENERATOR_ID, version: GENERATOR_VERSION, revision: GENERATOR_REVISION };