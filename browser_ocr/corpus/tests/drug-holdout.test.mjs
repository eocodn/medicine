import assert from "node:assert/strict";
import test from "node:test";

import {
  assignDrugPools,
  buildDrugCatalog,
  drugFamilyKey,
  normalizeDrugName,
  observedDrugLeakageReport,
} from "../drug_holdout.mjs";

function records(count = 180) {
  const result = [];
  for (let index = 0; index < count; index += 1) {
    result.push({
      item_seq: String(100000000 + index),
      product_name: `테스트약${String(index).padStart(3, "0")}정`,
    });
  }
  result.push(
    { item_seq: "900000001", product_name: "페니라민정(클로르페니라민말레산염)" },
    { item_seq: "900000002", product_name: "페니라민주사(클로르페니라민말레산염)" },
  );
  return result;
}

test("drug family normalization groups close dosage-form variants", () => {
  assert.equal(normalizeDrugName("  페니라민정  "), "페니라민정");
  assert.equal(
    drugFamilyKey("페니라민정(클로르페니라민말레산염)"),
    drugFamilyKey("페니라민주사(클로르페니라민말레산염)"),
  );
});

test("canonical drug assignment is deterministic and keeps exact names and near-name families in one pool", () => {
  const catalog = buildDrugCatalog(records());
  const first = assignDrugPools(catalog, { seed: 161 });
  const second = assignDrugPools(catalog, { seed: 161 });

  assert.equal(first.assignment_sha256, second.assignment_sha256);
  assert.deepEqual(first.pool_summaries, second.pool_summaries);
  for (const name of ["train", "val", "test"]) assert.ok(first.pools[name].length > 0);

  const ownerByName = new Map();
  const ownerByFamily = new Map();
  for (const split of ["train", "val", "test"]) {
    for (const product of first.pools[split]) {
      const normalized = normalizeDrugName(product.product_name);
      assert.equal(ownerByName.has(normalized), false, normalized);
      ownerByName.set(normalized, split);
      const existing = ownerByFamily.get(product.drug_family);
      if (existing) assert.equal(existing, split, product.drug_family);
      ownerByFamily.set(product.drug_family, split);
    }
  }

  assert.equal(
    ownerByFamily.get(drugFamilyKey("페니라민정(클로르페니라민말레산염)")),
    ownerByFamily.get(drugFamilyKey("페니라민주사(클로르페니라민말레산염)")),
  );
});

test("observed leakage audit fails closed on exact-name or family overlap", () => {
  const clean = observedDrugLeakageReport([
    { drug_name_split: "train", regions: [{ semantic_role: "product", text: "가나다정", drug_family: "family-a", drug_name_split: "train" }] },
    { drug_name_split: "test", regions: [{ semantic_role: "product", text: "라마바정", drug_family: "family-b", drug_name_split: "test" }] },
  ]);
  assert.equal(clean.status, "pass");

  const exactLeak = observedDrugLeakageReport([
    { drug_name_split: "train", regions: [{ semantic_role: "product", text: "가나다정", drug_family: "family-a", drug_name_split: "train" }] },
    { drug_name_split: "test", regions: [{ semantic_role: "product", text: "가나다정", drug_family: "family-a", drug_name_split: "test" }] },
  ]);
  assert.equal(exactLeak.status, "fail");
  assert.ok(exactLeak.failures.some((item) => item.includes("product name")));
  assert.ok(exactLeak.failures.some((item) => item.includes("drug family")));
});