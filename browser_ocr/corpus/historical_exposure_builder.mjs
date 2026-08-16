import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { createInterface } from "node:readline";

import { buildHistoricalDrugExposure, validateHistoricalDrugExposure } from "./drug_holdout.mjs";

const HISTORICAL_GENERATOR_VERSION = "5";
const PRODUCT_DECORATION_SUFFIX = / (?:5mg|10mg|100mg|500mg|0\.5mg|20mL|1정|TAB)$/u;

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

function safeDatasetPath(manifestPath, relativePath) {
  if (typeof relativePath !== "string" || !relativePath || relativePath.startsWith("/") || relativePath.split("/").includes("..")) {
    throw new Error("historical samples_file must be a safe relative path");
  }
  return resolve(dirname(manifestPath), relativePath);
}

async function readJsonObject(path, label) {
  let value;
  try {
    value = JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    throw new Error(`could not read ${label} ${path}: ${error.message}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object`);
  return value;
}

export function historicalStandaloneProductBase(text) {
  if (typeof text !== "string" || !text) throw new Error("historical product text must be non-empty");
  return text.replace(PRODUCT_DECORATION_SUFFIX, "");
}

async function atomicWriteJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, path);
}

export async function buildHistoricalExposureArtifact({ manifestPath, splitPath, checkpointSha256, outputPath }) {
  const manifestFile = resolve(manifestPath);
  const splitFile = resolve(splitPath);
  const outputFile = resolve(outputPath);
  const manifest = await readJsonObject(manifestFile, "historical recognition manifest");
  const splitContent = await readFile(splitFile);
  const split = JSON.parse(splitContent.toString("utf8"));
  if (!split || typeof split !== "object" || Array.isArray(split)) throw new Error("historical recognition split must be a JSON object");
  if (manifest.schema_version !== 1 || manifest.task !== "text_recognition") throw new Error("historical manifest must be text_recognition schema v1");
  if (manifest.metadata?.generator_version !== HISTORICAL_GENERATOR_VERSION) {
    throw new Error(`historical exposure builder requires standalone generator version ${HISTORICAL_GENERATOR_VERSION}`);
  }
  if (split.dataset_id !== manifest.dataset_id) throw new Error("historical split dataset_id does not match manifest");
  if (typeof split.dataset_fingerprint !== "string" || !/^[0-9a-f]{64}$/u.test(split.dataset_fingerprint)) {
    throw new Error("historical split dataset_fingerprint is invalid");
  }
  if (!split.splits || !Array.isArray(split.splits.train) || !split.splits.train.length) {
    throw new Error("historical split train membership is missing");
  }
  const trainIds = new Set(split.splits.train);
  if (trainIds.size !== split.splits.train.length) throw new Error("historical split train membership contains duplicates");
  if (split.counts?.train !== undefined && split.counts.train !== trainIds.size) throw new Error("historical split train count mismatch");

  const samplesFile = safeDatasetPath(manifestFile, manifest.samples_file);
  const seenTrainIds = new Set();
  const productNames = [];
  const input = createReadStream(samplesFile, { encoding: "utf8" });
  const lines = createInterface({ input, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line) throw new Error("historical samples file contains a blank line");
    const sample = JSON.parse(line);
    if (!trainIds.has(sample.id)) continue;
    if (seenTrainIds.has(sample.id)) throw new Error(`historical training sample id is duplicated: ${sample.id}`);
    seenTrainIds.add(sample.id);
    if (Array.isArray(sample.semantic_tags) && sample.semantic_tags.includes("product")) {
      productNames.push(historicalStandaloneProductBase(sample.text));
    }
  }
  if (seenTrainIds.size !== trainIds.size) {
    const missing = [...trainIds].find((id) => !seenTrainIds.has(id));
    throw new Error(`historical split references missing training sample: ${missing}`);
  }
  if (!productNames.length) throw new Error("historical training split contains no product-tagged samples");

  const exposure = buildHistoricalDrugExposure({
    productNames,
    checkpointSha256,
    sourceDatasetId: manifest.dataset_id,
    sourceDatasetFingerprint: split.dataset_fingerprint,
    sourceTrainSplitSha256: sha256(splitContent),
    sourceTrainSampleCount: trainIds.size,
  });
  const existing = await readFile(outputFile, "utf8").catch((error) => (error.code === "ENOENT" ? null : Promise.reject(error)));
  if (existing !== null) {
    const parsed = validateHistoricalDrugExposure(JSON.parse(existing));
    if (JSON.stringify(parsed) !== JSON.stringify(exposure)) throw new Error("historical exposure output exists with different authoritative content");
  } else {
    await atomicWriteJson(outputFile, exposure);
  }
  return {
    schema_version: 1,
    status: "completed",
    output: outputFile,
    output_sha256: sha256(await readFile(outputFile)),
    checkpoint_sha256: exposure.checkpoint_sha256,
    source_dataset_id: exposure.source_dataset_id,
    source_dataset_fingerprint: exposure.source_dataset_fingerprint,
    source_train_split_sha256: exposure.source_train_split_sha256,
    source_train_sample_count: exposure.source_train_sample_count,
    product_name_count: exposure.product_name_count,
    product_names_sha256: exposure.product_names_sha256,
    family_count: exposure.family_count,
    families_sha256: exposure.families_sha256,
  };
}
