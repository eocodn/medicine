import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const DRUG_SPLITS = ["train", "val", "test"];
export const DRUG_SPLIT_RATIOS = { train: 0.8, val: 0.1, test: 0.1 };
export const DRUG_NAME_POLICY_ID = "canonical-product-family-holdout-v1";

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

function splitForFamily(family, seed, ratios) {
  const digest = createHash("sha256").update(`medicine-drug-holdout-v1\0${seed}\0${family}`).digest();
  const value = digest.readUInt32BE(0) / 0x100000000;
  if (value < ratios.train) return "train";
  if (value < ratios.train + ratios.val) return "val";
  return "test";
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

export function assignDrugPools(catalog, { seed, ratios = DRUG_SPLIT_RATIOS } = {}) {
  if (!Array.isArray(catalog) || !catalog.length) throw new Error("drug catalog must be non-empty");
  if (!Number.isInteger(seed)) throw new Error("drug split seed must be an integer");
  const ratioTotal = DRUG_SPLITS.reduce((sum, name) => sum + Number(ratios[name]), 0);
  if (DRUG_SPLITS.some((name) => !Number.isFinite(ratios[name]) || ratios[name] <= 0 || ratios[name] >= 1)
    || Math.abs(ratioTotal - 1) > 1e-9) {
    throw new Error("drug split ratios must contain positive train/val/test values summing to one");
  }

  const familyOwner = new Map();
  const pools = { train: [], val: [], test: [] };
  for (const product of catalog) {
    const family = product.drug_family || drugFamilyKey(product.product_name);
    const split = familyOwner.get(family) || splitForFamily(family, seed, ratios);
    familyOwner.set(family, split);
    pools[split].push({ ...product, drug_family: family });
  }
  for (const split of DRUG_SPLITS) {
    pools[split].sort((left, right) => left.normalized_name.localeCompare(right.normalized_name));
    if (!pools[split].length) throw new Error(`drug split ${split} is empty`);
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
    pools,
    pool_summaries: poolSummaries,
    assignment_sha256: sha256(canonicalJson(assignment)),
  };
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