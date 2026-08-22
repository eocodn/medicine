import { createHash } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, rm, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { validateCorpus } from "./contract.mjs";
import {
  assignDrugPools,
  buildDrugCatalog,
  buildDrugNamePolicy,
  drugExposure,
  loadCanonicalDrugCatalog,
  loadHistoricalDrugExposure,
  normalizeDrugName,
  validateHistoricalDrugExposure,
} from "./drug_holdout.mjs";
import { appearanceForIndex, printerDescriptor, renderMaterialOverlay, renderPrinterOverlay } from "../detection/synthetic_appearance.mjs";
import { captureForSample, transformPolygonToImageBounds } from "../detection/synthetic_capture.mjs";
import {
  AUGMENTATION_DIFFICULTIES,
  BACKGROUND_PROFILES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
} from "../detection/synthetic_catalog.mjs";
import { buildDocumentTruth, SYNTHETIC_DOCUMENT_MODEL_VERSION } from "../detection/synthetic_document.mjs";
import { buildLayout, DOCUMENT_HEIGHT, DOCUMENT_WIDTH, renderLayoutRegions } from "../detection/synthetic_layouts.mjs";
import { rasterizerIdentity, renderRasterJpeg } from "../detection/synthetic_raster.mjs";
import { PARSER_STRUCTURE_REVISION, applyParserStructureVariant } from "./parser_structure.mjs";

const GENERATOR_ID = "medicine_full_document_synthetic";
const GENERATOR_VERSION = 6;
const GENERATOR_REVISION = 1;
const STATE_FILE = ".generation-state.json";
const LOCK_FILE = ".generation.lock";

const TASKS = ["detection", "recognition", "parsing", "e2e"];
const SPLIT_POLICY_ID = "document-cycle-v1";
const SPLIT_RATIOS = { train: 0.8, val: 0.1, test: 0.1 };

function splitForIndex(index, seed) {
  // Cycle-based assignment is stable as a corpus grows and keeps small corpora
  // from accidentally having no validation/test documents. The seed only
  // rotates the cycle; stage views always inherit this document-level split.
  const offset = ((seed % 10) + 10) % 10;
  const bucket = (index + offset) % 10;
  if (bucket < 8) return "train";
  if (bucket === 8) return "val";
  return "test";
}

function splitOrdinalForIndex(index, seed, split) {
  const fullCycles = Math.floor(index / 10);
  const perCycle = split === "train" ? 8 : 1;
  let ordinal = fullCycles * perCycle;
  const cycleStart = fullCycles * 10;
  for (let candidate = cycleStart; candidate < index; candidate += 1) {
    if (splitForIndex(candidate, seed) === split) ordinal += 1;
  }
  return ordinal;
}

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

async function configurationFingerprint({ seed, count, drugNamePolicy }) {
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
      augmentation_difficulties: AUGMENTATION_DIFFICULTIES,
      material_profiles: MATERIAL_PROFILES,
      printer_profiles: PRINTER_PROFILES,
      background_profiles: BACKGROUND_PROFILES,
      document_model_version: SYNTHETIC_DOCUMENT_MODEL_VERSION,
      tasks: TASKS,
      split_policy: { id: SPLIT_POLICY_ID, ratios: SPLIT_RATIOS },
      drug_name_policy: drugNamePolicy,
      parser_structure_revision: PARSER_STRUCTURE_REVISION,
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

function buildSamplePlan(index, seed, drugAssignment) {
  const baseSeed = sampleSeed(seed, index);
  // Independent deterministic streams keep semantic content, layout geometry, and capture severity
  // stable when another layer evolves.
  const documentRandom = rng(baseSeed ^ 0x2f6e2b1d);
  const layoutRandom = rng(baseSeed ^ 0xa511e9b3);
  const captureRandom = rng(baseSeed ^ 0x63d83595);
  const split = splitForIndex(index, seed);
  const splitOrdinal = splitOrdinalForIndex(index, seed, split);
  const drugPool = drugAssignment.pools[split];
  if (!Array.isArray(drugPool) || drugPool.length === 0) throw new Error(`drug pool ${split} is empty`);
  const productMetadata = new Map(drugPool.map((product) => [normalizeDrugName(product.product_name), product]));
  const layoutFamily = LAYOUT_FAMILIES[index % LAYOUT_FAMILIES.length];
  const document = buildDocumentTruth(index, documentRandom, {
    products: drugPool.map((product) => product.product_name),
    layoutFamily,
  });
  const baseLayout = buildLayout(index, layoutRandom, { document });
  const layout = applyParserStructureVariant(baseLayout, { index, split, splitOrdinal, random: layoutRandom });
  const captureIndex = Math.floor(index / LAYOUT_FAMILIES.length) % CAPTURE_PROFILES.length;
  const capture = captureForSample(index, captureIndex, captureRandom, DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  const appearance = appearanceForIndex(index);
  const id = `synthetic-${String(index + 1).padStart(6, "0")}`;
  const image = `images/${id}.jpg`;
  const regions = layout.regions.map((item) => {
    const {
      natural_text_box: naturalTextBox,
      layout_slot: layoutSlot,
      text_origin: textOrigin,
      ...rest
    } = item;
    const drugMetadata = item.semantic_role === "product"
      ? productMetadata.get(normalizeDrugName(item.text))
      : null;
    if (item.semantic_role === "product" && !drugMetadata) {
      throw new Error(`layout emitted product outside assigned ${split} drug pool: ${item.text}`);
    }
    return {
      ...rest,
      ...(drugMetadata ? { drug_name_split: split, drug_family: drugMetadata.drug_family } : {}),
      source_text_origin: [...textOrigin],
      source_layout_slot: layoutSlot.map((point) => [...point]),
      source_natural_text_polygon: naturalTextBox.map((point) => [...point]),
      natural_text_polygon: transformPolygonToImageBounds(capture.homography, naturalTextBox, DOCUMENT_WIDTH, DOCUMENT_HEIGHT),
      source_polygon: item.polygon.map((point) => [...point]),
      polygon: transformPolygonToImageBounds(capture.homography, item.polygon, DOCUMENT_WIDTH, DOCUMENT_HEIGHT),
    };
  });
  return {
    sample: {
      id,
      image,
      width: DOCUMENT_WIDTH,
      height: DOCUMENT_HEIGHT,
      sample_index: index,
      split,
      drug_name_split: split,
      drug_name_exposure: drugExposure(split),
      document_type: document.document_type,
      layout_family: layout.layout_family,
      parser_structure_variant: layout.parser_structure_variant,
      capture_profile: capture.profile,
      augmentation_difficulty: capture.difficulty,
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

async function renderSample(index, seed, outputDir, drugAssignment) {
  const plan = buildSamplePlan(index, seed, drugAssignment);
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

function buildCorpus({ seed, count, fingerprint, rasterizer, drugNamePolicy, samples }) {
  return validateCorpus({
    schema_version: 3,
    corpus_id: `synthetic-medicine-document-v6-seed-${seed}-drug-seed-${drugNamePolicy.assignment_seed}-hist-${drugNamePolicy.historical_exposure.families_sha256.slice(0, 12)}-n-${count}`,
    synthetic_only: true,
    tasks: [...TASKS],
    split_policy: {
      id: SPLIT_POLICY_ID,
      seed,
      ratios: { ...SPLIT_RATIOS },
    },
    drug_name_policy: drugNamePolicy,
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
    schema_version: 3,
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

async function verifyCheckpointState(outputDir, state, seed, drugAssignment) {
  for (const [index, sample] of state.samples.entries()) {
    const expected = buildSamplePlan(index, seed, drugAssignment).sample;
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

export function generationCheckpointInterval(count) {
  // The checkpoint embeds every completed sample, so rewriting it per image
  // becomes quadratic at scale. A crash may redo only this bounded tail; the
  // deterministic image paths are safely overwritten during resume.
  return Math.min(50, Math.max(1, Math.floor(count / 20)));
}

async function acquireLock(path) {
  try {
    return await open(path, "wx");
  } catch (error) {
    if (error && error.code === "EEXIST") throw new Error("synthetic generation is already running for this output directory");
    throw error;
  }
}

function injectedDrugSource(catalog) {
  const sourceSha = digest(JSON.stringify(catalog.map((product) => [product.item_seq, product.product_name])));
  return {
    products: catalog,
    canonical_db_sha256: sourceSha,
    source: {
      dataset_key: "injected:drug-catalog",
      source_family: "explicit_test_or_experiment_catalog",
      source_locator: "in-memory",
      sha256: sourceSha,
    },
  };
}

async function drugConfiguration({ canonicalDb, drugCatalog, drugSplitSeed, historicalDrugExposure }) {
  if (canonicalDb && drugCatalog) throw new Error("provide either canonicalDb or drugCatalog, not both");
  if (!canonicalDb && !drugCatalog) throw new Error("canonicalDb is required unless an explicit drugCatalog is provided");
  if (!Number.isInteger(drugSplitSeed)) throw new Error("drugSplitSeed must be an integer");
  if (!historicalDrugExposure) throw new Error("historicalDrugExposure is required");
  const loaded = drugCatalog
    ? injectedDrugSource(buildDrugCatalog(drugCatalog))
    : await loadCanonicalDrugCatalog(canonicalDb);
  const exposure = typeof historicalDrugExposure === "string"
    ? await loadHistoricalDrugExposure(historicalDrugExposure)
    : validateHistoricalDrugExposure(historicalDrugExposure);
  const assignment = assignDrugPools(loaded.products, { seed: drugSplitSeed, historicalExposure: exposure });
  const policy = buildDrugNamePolicy({
    catalog: loaded.products,
    assignment,
    source: loaded.source,
    canonicalDbSha256: loaded.canonical_db_sha256,
  });
  return { assignment, policy };
}

export async function generateUnifiedCorpus({
  outputDir,
  count = 36,
  seed = 153,
  drugSplitSeed = null,
  historicalDrugExposure = null,
  canonicalDb = null,
  drugCatalog = null,
  onProgress = null,
}) {
  if (!Number.isInteger(count) || count <= 0) throw new Error("count must be a positive integer");
  if (!Number.isInteger(seed)) throw new Error("seed must be an integer");
  await mkdir(join(outputDir, "images"), { recursive: true });

  const { assignment: drugAssignment, policy: drugNamePolicy } = await drugConfiguration({
    canonicalDb, drugCatalog, drugSplitSeed, historicalDrugExposure,
  });
  const { fingerprint, rasterizer } = await configurationFingerprint({ seed, count, drugNamePolicy });
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
      await verifyCheckpointState(outputDir, state, seed, drugAssignment);
    } else {
      const existingImages = await readdir(join(outputDir, "images"));
      if (existingImages.length) throw new Error("output images directory is non-empty without a generation checkpoint");
      state = stateFor({ seed, count, fingerprint, rasterizer });
      await atomicWrite(statePath, `${JSON.stringify(state, null, 2)}\n`);
    }

    const checkpointInterval = generationCheckpointInterval(count);
    for (let index = state.completed; index < count; index += 1) {
      const sample = await renderSample(index, seed, outputDir, drugAssignment);
      state.samples.push(sample);
      state.completed = state.samples.length;
      if (state.completed === count || state.completed % checkpointInterval === 0) {
        await atomicWrite(statePath, `${JSON.stringify(state, null, 2)}\n`);
      }

      const event = {
        completed: state.completed,
        total: count,
        sample_id: sample.id,
        layout_family: sample.layout_family,
        capture_profile: sample.capture_profile,
        augmentation_difficulty: sample.augmentation_difficulty,
        material_profile: sample.material_profile,
      };
      if (shouldReport(state.completed, count)) {
        const percent = Math.round(state.completed * 100 / count);
        process.stderr.write(`[ocr-synth] ${state.completed}/${count} ${percent}% ${sample.layout_family}/${sample.capture_profile}/${sample.material_profile}\n`);
      }
      if (onProgress) onProgress(event);
    }

    const corpus = buildCorpus({ seed, count, fingerprint, rasterizer, drugNamePolicy, samples: state.samples });
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
export const generateSyntheticCorpus = generateUnifiedCorpus;
