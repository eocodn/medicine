import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const DRUG_SPLITS = ["train", "val", "test"];
export const DRUG_SPLIT_RATIOS = { train: 0.8, val: 0.1, test: 0.1 };
export const DRUG_NAME_POLICY_ID = "canonical-product-family-historical-holdout-v2";
export const HISTORICAL_EXPOSURE_ID = "selected-recognizer-training-exposure-v1";

const SOURCE_DATASET_KEY = "mfds_permit:products";
const MAX_PRODUCT_LENGTH = 18;
const DOSAGE_FORM_SUFFIXES = [
  "흡입용캡슐", "연질캡슐", "경질캡슐", "주사용수", "생리식염주사액", "점안액", "점비액",
  "현탁액", "주사액", "시럽", "과립", "플라스타", "패취", "크림", "연고", "겔",
  "캡슐", "캅셀", "주사", "정제", "정", "액", "산", "주",
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function cleanText(value, maximumLength) {
  if (typeof value !== "string") return null;
  const text = value.normalize("NFKC").normalize("NFC").trim();
  if (!text || text.length > maximumLength || /[\t\r\n\0]/u.test(text)) return null;
  return text;
}

export function normalizeDrugName(value) {
  const text = cleanText(String(value ?? ""), 4096);
  if (!text) return "";
  return text.toLocaleLowerCase("ko-KR").replace(/\s+/gu, " ").trim();
}

function familyStem(value) {
  let text = normalizeDrugName(value);
  text = text
    .replace(/\([^)]*\)/gu, "")
    .replace(/\[[^\]]*\]/gu, "")
    .replace(/<[^>]*>/gu, "")
    .replace(/\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|밀리그램|마이크로그램|그램|밀리리터|아이유|%)/giu, "")
    .replace(/[\s·._\-_/,:;%]+/gu, "");
  let changed = true;
  while (changed && text.length > 1) {
    changed = false;
    for (const suffix of DOSAGE_FORM_SUFFIXES) {
      if (text.endsWith(suffix) && text.length > suffix.length + 1) {
        text = text.slice(0, -suffix.length);
        changed = true;
        break;
      }
    }
  }
  return text || normalizeDrugName(value).replace(/\s+/gu, "");
}

export function drugFamilyKey(value) {
  return `family-${sha256(`medicine-drug-family-v1\0${familyStem(value)}`).slice(0, 20)}`;
}

function requireSha256(value, label) {
  if (typeof value !== "string" || !/^[0-9a-f]{64}$/u.test(value)) throw new Error(`${label} must be lowercase SHA-256`);
  return value;
}

export function buildHistoricalDrugExposure({
  productNames,
  checkpointSha256,
  sourceDatasetId,
  sourceDatasetFingerprint,
  sourceTrainSplitSha256,
  sourceTrainSampleCount,
}) {
  if (!Array.isArray(productNames) || !productNames.length) throw new Error("historical productNames must be non-empty");
  if (typeof sourceDatasetId !== "string" || !sourceDatasetId.trim()) throw new Error("historical sourceDatasetId is required");
  if (!Number.isInteger(sourceTrainSampleCount) || sourceTrainSampleCount <= 0) throw new Error("historical sourceTrainSampleCount must be positive");
  const names = [...new Set(productNames.map(normalizeDrugName).filter(Boolean))].sort();
  if (!names.length) throw new Error("historical productNames contain no valid names");
  const families = [...new Set(names.map(drugFamilyKey))].sort();
  return {
    id: HISTORICAL_EXPOSURE_ID,
    checkpoint_sha256: requireSha256(checkpointSha256, "historical checkpoint_sha256"),
    source_dataset_id: sourceDatasetId.trim(),
    source_dataset_fingerprint: requireSha256(sourceDatasetFingerprint, "historical source_dataset_fingerprint"),
    source_train_split_sha256: requireSha256(sourceTrainSplitSha256, "historical source_train_split_sha256"),
    source_train_sample_count: sourceTrainSampleCount,
    product_name_count: names.length,
    product_names_sha256: sha256(names.join("\n")),
    family_count: families.length,
    families_sha256: sha256(families.join("\n")),
    product_names: names,
    families,
  };
}

export function validateHistoricalDrugExposure(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("historical drug exposure must be an object");
  if (value.id !== HISTORICAL_EXPOSURE_ID) throw new Error(`historical drug exposure id must be ${HISTORICAL_EXPOSURE_ID}`);
  for (const key of ["checkpoint_sha256", "source_dataset_fingerprint", "source_train_split_sha256", "product_names_sha256", "families_sha256"]) {
    requireSha256(value[key], `historical ${key}`);
  }
  if (typeof value.source_dataset_id !== "string" || !value.source_dataset_id.trim()) throw new Error("historical source_dataset_id is required");
  for (const key of ["source_train_sample_count", "product_name_count", "family_count"]) {
    if (!Number.isInteger(value[key]) || value[key] <= 0) throw new Error(`historical ${key} must be positive`);
  }
  if (!Array.isArray(value.product_names) || value.product_names.length !== value.product_name_count) throw new Error("historical product_names count mismatch");
  if (!Array.isArray(value.families) || value.families.length !== value.family_count) throw new Error("historical families count mismatch");
  const names = [...value.product_names];
  const families = [...value.families];
  if (names.some((name) => normalizeDrugName(name) !== name) || new Set(names).size !== names.length || [...names].sort().some((name, index) => name !== names[index])) {
    throw new Error("historical product_names must be unique sorted normalized names");
  }
  if (families.some((family) => typeof family !== "string" || !/^family-[0-9a-f]{20}$/u.test(family))
    || new Set(families).size !== families.length || [...families].sort().some((family, index) => family !== families[index])) {
    throw new Error("historical families must be unique sorted family keys");
  }
  if (sha256(names.join("\n")) !== value.product_names_sha256) throw new Error("historical product_names_sha256 mismatch");
  if (sha256(families.join("\n")) !== value.families_sha256) throw new Error("historical families_sha256 mismatch");
  const derivedFamilies = [...new Set(names.map(drugFamilyKey))].sort();
  if (derivedFamilies.length !== families.length || derivedFamilies.some((family, index) => family !== families[index])) {
    throw new Error("historical families do not match product_names");
  }
  return structuredClone(value);
}

export function historicalExposureSummary(value) {
  const exposure = validateHistoricalDrugExposure(value);
  const { product_names: _productNames, families: _families, ...summary } = exposure;
  return summary;
}

export function buildDrugCatalog(records) {
  if (!Array.isArray(records)) throw new Error("drug catalog records must be an array");
  const byName = new Map();
  for (const record of records) {
    if (!record || typeof record !== "object" || Array.isArray(record)) continue;
    const productName = cleanText(record.product_name, MAX_PRODUCT_LENGTH);
    const itemSeq = cleanText(record.item_seq, 64);
    if (!productName || !itemSeq) continue;
    const normalizedName = normalizeDrugName(productName);
    const product = {
      item_seq: itemSeq,
      product_name: productName,
      normalized_name: normalizedName,
      drug_family: drugFamilyKey(productName),
    };
    const existing = byName.get(normalizedName);
    if (!existing || itemSeq.localeCompare(existing.item_seq) < 0) byName.set(normalizedName, product);
  }
  const products = [...byName.values()].sort((left, right) => (
    left.normalized_name.localeCompare(right.normalized_name) || left.item_seq.localeCompare(right.item_seq)
  ));
  if (products.length < 3) throw new Error("canonical drug catalog must contain at least three eligible product names");
  return products;
}

function familyOrderHash(family, seed) {
  return sha256(`medicine-drug-holdout-v2\0${seed}\0${family}`);
}

function poolSummary(products) {
  const names = products.map((product) => product.normalized_name).sort();
  const families = [...new Set(products.map((product) => product.drug_family))].sort();
  return {
    product_count: products.length,
    family_count: families.length,
    product_names_sha256: sha256(names.join("\n")),
    families_sha256: sha256(families.join("\n")),
  };
}

function groupedFamilies(catalog) {
  const groups = new Map();
  for (const product of catalog) {
    const family = product.drug_family || drugFamilyKey(product.product_name);
    const current = groups.get(family) || [];
    current.push({ ...product, drug_family: family });
    groups.set(family, current);
  }
  return [...groups.entries()].map(([family, products]) => ({ family, products }));
}

export function assignDrugPools(catalog, { seed, ratios = DRUG_SPLIT_RATIOS, historicalExposure } = {}) {
  if (!Array.isArray(catalog) || !catalog.length) throw new Error("drug catalog must be non-empty");
  if (!Number.isInteger(seed)) throw new Error("drug split seed must be an integer");
  const exposure = validateHistoricalDrugExposure(historicalExposure);
  const ratioTotal = DRUG_SPLITS.reduce((sum, name) => sum + Number(ratios[name]), 0);
  if (DRUG_SPLITS.some((name) => !Number.isFinite(ratios[name]) || ratios[name] <= 0 || ratios[name] >= 1)
    || Math.abs(ratioTotal - 1) > 1e-9) {
    throw new Error("drug split ratios must contain positive train/val/test values summing to one");
  }

  const historicalFamilies = new Set(exposure.families);
  const historicalNames = new Set(exposure.product_names);
  const groups = groupedFamilies(catalog);
  const pools = { train: [], val: [], test: [] };
  const unseenGroups = [];
  for (const group of groups) {
    if (historicalFamilies.has(group.family)) pools.train.push(...group.products);
    else unseenGroups.push(group);
  }
  if (!pools.train.length || unseenGroups.length < 2) {
    throw new Error("historical exposure must leave non-empty seen train and at least two unseen drug families");
  }

  // The selected recognizer's real training exposure defines the semantic boundary.
  // Do not pad train to an 80% vocabulary target with historically unseen families:
  // that would make the `seen` label false. Document train/val/test proportions remain
  // controlled independently by split_policy; the unseen vocabulary is balanced here.
  unseenGroups.sort((left, right) => (
    right.products.length - left.products.length
    || familyOrderHash(left.family, seed).localeCompare(familyOrderHash(right.family, seed))
    || left.family.localeCompare(right.family)
  ));
  const unseenCounts = { val: 0, test: 0 };
  for (const group of unseenGroups) {
    const split = unseenCounts.val <= unseenCounts.test ? "val" : "test";
    pools[split].push(...group.products);
    unseenCounts[split] += group.products.length;
  }

  for (const split of DRUG_SPLITS) {
    pools[split].sort((left, right) => left.normalized_name.localeCompare(right.normalized_name));
    if (!pools[split].length) throw new Error(`drug split ${split} is empty`);
  }
  for (const product of pools.train) {
    if (!historicalFamilies.has(product.drug_family)) throw new Error(`historically unseen drug family leaked into train: ${product.drug_family}`);
  }
  for (const split of ["val", "test"]) {
    for (const product of pools[split]) {
      if (historicalFamilies.has(product.drug_family)) throw new Error(`historical drug family leaked into ${split}: ${product.drug_family}`);
      if (historicalNames.has(product.normalized_name)) throw new Error(`historical product name leaked into ${split}: ${product.normalized_name}`);
    }
  }
  const poolSummaries = Object.fromEntries(DRUG_SPLITS.map((split) => [split, poolSummary(pools[split])]));
  const assignment = DRUG_SPLITS.flatMap((split) => pools[split].map((product) => ({
    split,
    name: product.normalized_name,
    family: product.drug_family,
  })));
  return {
    seed,
    ratios: { ...ratios },
    pool_assignment_rule: "historically-exposed-train-unseen-balanced-val-test-v1",
    pools,
    pool_summaries: poolSummaries,
    assignment_sha256: sha256(canonicalJson(assignment)),
    historical_exposure: historicalExposureSummary(exposure),
  };
}

export async function loadHistoricalDrugExposure(pathValue) {
  const path = resolve(pathValue);
  let value;
  try {
    value = JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    throw new Error(`could not read historical drug exposure ${path}: ${error.message}`);
  }
  return validateHistoricalDrugExposure(value);
}

export async function loadCanonicalDrugCatalog(canonicalDb) {
  const path = resolve(canonicalDb);
  const content = await readFile(path);
  const canonicalDbSha256 = sha256(content);
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(path, { readOnly: true });
  try {
    const source = database.prepare(
      "select dataset_key, source_family, source_locator, sha256 from source_snapshots where dataset_key = ?",
    ).get(SOURCE_DATASET_KEY);
    if (!source) throw new Error(`canonical database is missing source snapshot ${SOURCE_DATASET_KEY}`);
    if (!/^[0-9a-f]{64}$/u.test(String(source.sha256 || ""))) {
      throw new Error("canonical source snapshot has an invalid SHA-256");
    }
    const rows = database.prepare(
      "select item_seq, product_name from products where source_dataset_key = ? "
      + "and permit_status not in ('canceled', 'withdrawn', 'expired', 'business_closed') order by item_seq",
    ).all(SOURCE_DATASET_KEY);
    const products = buildDrugCatalog(rows);
    return {
      products,
      canonical_db_sha256: canonicalDbSha256,
      source: {
        dataset_key: String(source.dataset_key),
        source_family: String(source.source_family),
        source_locator: String(source.source_locator),
        sha256: String(source.sha256),
      },
    };
  } finally {
    database.close();
  }
}

export function buildDrugNamePolicy({ catalog, assignment, source, canonicalDbSha256 }) {
  if (!Array.isArray(catalog) || !assignment || !source) throw new Error("drug name policy inputs are incomplete");
  return {
    id: DRUG_NAME_POLICY_ID,
    assignment_seed: assignment.seed,
    ratios: { ...assignment.ratios },
    family_rule: "normalized-base-name-with-dosage-form-and-strength-stripping-v1",
    source: {
      ...source,
      canonical_db_sha256: canonicalDbSha256,
    },
    eligible_product_count: catalog.length,
    eligible_family_count: new Set(catalog.map((product) => product.drug_family)).size,
    assignment_sha256: assignment.assignment_sha256,
    pool_assignment_rule: assignment.pool_assignment_rule,
    historical_exposure: structuredClone(assignment.historical_exposure),
    pools: structuredClone(assignment.pool_summaries),
  };
}

export function drugExposure(split) {
  if (!DRUG_SPLITS.includes(split)) throw new Error(`unsupported drug split ${split}`);
  return split === "train" ? "seen" : "unseen";
}

export function observedDrugLeakageReport(samples) {
  const names = new Map();
  const families = new Map();
  const observedNames = { train: new Set(), val: new Set(), test: new Set() };
  const observedFamilies = { train: new Set(), val: new Set(), test: new Set() };
  const failures = [];

  for (const [sampleIndex, sample] of samples.entries()) {
    const sampleSplit = sample?.drug_name_split;
    if (!DRUG_SPLITS.includes(sampleSplit)) {
      failures.push(`sample ${sample?.id || sampleIndex} has invalid drug_name_split`);
      continue;
    }
    for (const region of sample.regions || []) {
      if (region.semantic_role !== "product") continue;
      if (region.drug_name_split !== sampleSplit) {
        failures.push(`${sample?.id || sampleIndex}.${region.region_id || "product"} drug_name_split differs from parent document`);
      }
      const normalized = normalizeDrugName(region.text);
      if (!normalized) failures.push(`${sample?.id || sampleIndex}.${region.region_id || "product"} has empty normalized product name`);
      const family = typeof region.drug_family === "string" ? region.drug_family.trim() : "";
      if (!family) failures.push(`${sample?.id || sampleIndex}.${region.region_id || "product"} is missing drug_family`);
      if (normalized) {
        observedNames[sampleSplit].add(normalized);
        const owners = names.get(normalized) || new Set();
        owners.add(sampleSplit);
        names.set(normalized, owners);
      }
      if (family) {
        observedFamilies[sampleSplit].add(family);
        const owners = families.get(family) || new Set();
        owners.add(sampleSplit);
        families.set(family, owners);
      }
    }
  }
  for (const [name, owners] of names) {
    if (owners.size > 1) failures.push(`product name ${name} leaks across ${[...owners].sort().join(",")}`);
  }
  for (const [family, owners] of families) {
    if (owners.size > 1) failures.push(`drug family ${family} leaks across ${[...owners].sort().join(",")}`);
  }
  return {
    status: failures.length ? "fail" : "pass",
    observed_product_names: Object.fromEntries(DRUG_SPLITS.map((split) => [split, observedNames[split].size])),
    observed_families: Object.fromEntries(DRUG_SPLITS.map((split) => [split, observedFamilies[split].size])),
    failures: failures.sort(),
  };
}