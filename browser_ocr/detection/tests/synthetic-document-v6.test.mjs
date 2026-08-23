import assert from "node:assert/strict";
import test from "node:test";

import { LAYOUT_FAMILIES, PAGE_ROTATIONS } from "../synthetic_catalog.mjs";
import { captureForSample } from "../synthetic_capture.mjs";
import { buildDocumentTruth } from "../synthetic_document.mjs";
import { buildLayout, DOCUMENT_HEIGHT, DOCUMENT_WIDTH, renderLayoutRegions } from "../synthetic_layouts.mjs";
import {
  PHARMACY_GUIDE_STYLE_IDS,
  pharmacyGuideStyleForIndex,
} from "../synthetic_pharmacy_guide_styles.mjs";

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

test("receipt-sidecar cycles distinct real-print style families and medication-row densities", () => {
  assert.equal(PHARMACY_GUIDE_STYLE_IDS.length, 10);
  const observedStyles = new Set();
  const observedMedicationCounts = new Set();
  const expectedTopologyMarkers = new Map([
    ["teal_modern_grid", "guide-template-teal-modern-grid"],
    ["pediatric_pastel", "guide-template-pediatric-pastel"],
    ["monochrome_stapled", "guide-template-monochrome-stapled"],
    ["lavender_dense", "guide-template-lavender-dense"],
    ["yellow_blue_split", "guide-template-yellow-blue-split"],
  ]);
  for (let index = 0; index < 70; index += 1) {
    const random = seeded(900 + index);
    const document = buildDocumentTruth(index, random, {
      products: PRODUCTS,
      layoutFamily: "pharmacy_guide_receipt_sidecar",
    });
    const layout = buildLayout(index, random, { document });
    observedStyles.add(layout.visual_style);
    observedMedicationCounts.add(document.medications.length);
    assert.equal(layout.visual_style, pharmacyGuideStyleForIndex(index).id);
    assert.ok(layout.scenario_tags.includes(`print_style_${layout.visual_style}`));
    assert.match(layout.decorations, /medication-thumbnail/u);
    assert.match(layout.decorations, /pill-shadow/u);
    assert.match(layout.decorations, /thumbnail-grain/u);
    assert.match(layout.decorations, /receipt-perforation/u);
    assert.match(layout.decorations, /qr-distractor/u);
    assert.match(layout.decorations, /guide-lower-(?:schedule|warning|storage|pediatric|monochrome|triple-info|compact-schedule)-panel/u);
    assert.ok(layout.regions.some((region) => region.region_id.startsWith("guide-lower-")));
    const topologyMarker = expectedTopologyMarkers.get(layout.visual_style);
    if (topologyMarker) assert.match(layout.decorations, new RegExp(topologyMarker, "u"));
    const headers = layout.regions.filter((region) => region.region_id.startsWith("guide-") && region.semantic_role === "header");
    if (["blue_striped", "navy_dense_guide"].includes(layout.visual_style)) {
      assert.ok(headers.length > 0);
      assert.ok(headers.every((region) => region.text_fill === "#f7f9fb"));
      assert.match(renderLayoutRegions(headers), /fill="#f7f9fb"/u);
    }
    if (layout.visual_style === "navy_dense_guide") {
      const lowerTitle = layout.regions.find((region) => region.region_id === "guide-lower-title");
      assert.equal(lowerTitle?.text_fill, "#f7f9fb");
      assert.match(renderLayoutRegions([lowerTitle]), /fill="#f7f9fb"/u);
    }
    if (layout.visual_style === "teal_modern_grid") {
      assert.ok(layout.regions.some((region) => region.region_id === "guide-template-promo-title"));
      assert.ok(layout.regions.some((region) => region.region_id === "guide-template-promo-note"));
    }
    if (layout.visual_style === "monochrome_stapled") {
      const title = layout.regions.find((region) => region.region_id === "receipt-title");
      const staple = layout.decorations.match(/class="receipt-staple"[^>]*y1="([0-9.]+)"[^>]*y2="([0-9.]+)"/u);
      assert.ok(title && staple);
      assert.ok(Math.max(Number(staple[1]), Number(staple[2])) < title.natural_text_box[0][1]);
    }
    if (layout.visual_style === "lavender_dense") {
      const lowerInfo = layout.regions.filter((region) => region.region_id.startsWith("guide-lower-info-"));
      assert.ok(lowerInfo.length >= 3);
      assert.ok(lowerInfo.every((region) => !region.text.includes("\n")));
    }
  }
  assert.deepEqual(observedStyles, new Set(PHARMACY_GUIDE_STYLE_IDS));
  assert.deepEqual(observedMedicationCounts, new Set([1, 2, 3, 4, 5]));
});

test("v6 capture cycle includes metadata-free right-angle full-page rotations", () => {
  const captures = Array.from({ length: LAYOUT_FAMILIES.length * 6 }, (_, index) => {
    const profileIndex = Math.floor(index / LAYOUT_FAMILIES.length) % 6;
    return captureForSample(index, profileIndex, seeded(8100 + index), DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  });
  assert.deepEqual(new Set(captures.map((capture) => capture.page_rotation_degrees)), new Set(PAGE_ROTATIONS));
  assert.ok(captures.filter((capture) => capture.page_rotation_degrees !== 0).every((capture) => (
    capture.augmentation_components.includes("right_angle_rotation")
    && capture.risk_tags.includes("page_rotation")
  )));

  const rotated = captureForSample(2, 0, () => 0.99, DOCUMENT_WIDTH, DOCUMENT_HEIGHT);
  assert.equal(rotated.page_rotation_degrees, 90);
  const xs = rotated.destination_corners.map(([x]) => x);
  const ys = rotated.destination_corners.map(([, y]) => y);
  assert.ok(Math.max(...xs) - Math.min(...xs) > Math.max(...ys) - Math.min(...ys));
});