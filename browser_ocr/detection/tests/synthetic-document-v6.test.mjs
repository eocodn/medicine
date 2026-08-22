import assert from "node:assert/strict";
import test from "node:test";

import { buildDocumentTruth } from "../synthetic_document.mjs";
import { buildLayout } from "../synthetic_layouts.mjs";

function seeded(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

const PRODUCTS = [
  "아모잘탄정5/50밀리그램",
  "레바미피드정100밀리그램",
  "세티리진염산염정10밀리그램",
  "클로피도그렐황산염정75밀리그램",
];

test("v6 document truth is semantic and contains no rendered geometry", () => {
  const document = buildDocumentTruth(0, seeded(11), {
    products: PRODUCTS,
    layoutFamily: "prescription_table",
  });

  assert.equal(document.schema_version, 1);
  assert.equal(document.layout_family, "prescription_table");
  assert.ok(document.medications.length >= 3);
  assert.ok(document.medications.every((item) => item.id.startsWith("med-")));
  assert.ok(document.medications.every((item) => PRODUCTS.includes(item.product)));

  const forbiddenKeys = new Set(["polygon", "text_origin", "font_size_px", "layout_slot", "x", "y", "width", "height"]);
  const visit = (value) => {
    if (Array.isArray(value)) return value.forEach(visit);
    if (!value || typeof value !== "object") return;
    for (const [key, nested] of Object.entries(value)) {
      assert.equal(forbiddenKeys.has(key), false, key);
      visit(nested);
    }
  };
  visit(document);
});

test("receipt-sidecar layout derives medication truth and keeps accounting numerics as distractors", () => {
  const random = seeded(73);
  const document = buildDocumentTruth(6, random, {
    products: PRODUCTS,
    layoutFamily: "pharmacy_guide_receipt_sidecar",
  });
  const layout = buildLayout(6, random, { document });

  assert.equal(layout.layout_family, "pharmacy_guide_receipt_sidecar");
  assert.ok(layout.scenario_tags.includes("receipt_sidecar"));
  assert.ok(layout.risk_tags.includes("numeric_distractor"));

  const medication = document.medications[0];
  const medicationRegions = layout.regions.filter((region) => region.association_group === medication.id);
  assert.equal(medicationRegions.find((region) => region.semantic_role === "product")?.text, medication.product);
  assert.equal(medicationRegions.find((region) => region.semantic_role === "dose")?.text, medication.dose_text);
  assert.equal(medicationRegions.find((region) => region.semantic_role === "frequency")?.text, medication.frequency_text);
  assert.equal(medicationRegions.find((region) => region.semantic_role === "duration")?.text, medication.duration_text);

  const receiptRegions = layout.regions.filter((region) => region.region_id.startsWith("receipt-"));
  assert.ok(receiptRegions.length >= 12);
  assert.ok(receiptRegions.every((region) => region.association_group === "document"));
  assert.ok(receiptRegions.every((region) => region.region_class === "distractor"));
  assert.ok(receiptRegions.some((region) => /^\d{1,3}(?:,\d{3})*$/u.test(region.text)));
  assert.ok(receiptRegions.some((region) => /^\d+$/u.test(region.text)));
});