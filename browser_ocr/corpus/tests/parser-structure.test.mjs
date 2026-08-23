import assert from "node:assert/strict";
import test from "node:test";

import { LAYOUT_FAMILIES } from "../../detection/synthetic_catalog.mjs";
import { buildDocumentTruth } from "../../detection/synthetic_document.mjs";
import { buildLayout } from "../../detection/synthetic_layouts.mjs";
import { expectedRows, parserTrainingRows } from "../parser_truth.mjs";
import {
  PARSER_STRUCTURE_VARIANTS,
  applyParserStructureVariant,
  parserStructureVariantForSample,
} from "../parser_structure.mjs";

function rng(seed = 17) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function products() {
  return Array.from({ length: 12 }, (_, index) => `검증약${index}정`);
}

function semanticLayout(index, random, layoutFamily) {
  const document = buildDocumentTruth(index, random, { products: products(), layoutFamily });
  return buildLayout(index, random, { document });
}

function boxOverlap(a, b) {
  const [a0, , a2] = a;
  const [b0, , b2] = b;
  return a0[0] < b2[0] && a2[0] > b0[0] && a0[1] < b2[1] && a2[1] > b0[1];
}

test("parser structure recipes are split-specific and train covers its recipe pool", () => {
  const train = new Set(PARSER_STRUCTURE_VARIANTS.train);
  const val = new Set(PARSER_STRUCTURE_VARIANTS.val);
  const heldout = new Set(PARSER_STRUCTURE_VARIANTS.test);
  assert.equal([...train].some((value) => val.has(value) || heldout.has(value)), false);
  assert.equal([...val].some((value) => heldout.has(value)), false);
  assert.deepEqual(
    new Set(Array.from(
      { length: PARSER_STRUCTURE_VARIANTS.train.length },
      (_, offset) => parserStructureVariantForSample(offset + LAYOUT_FAMILIES.length, "train"),
    )),
    train,
  );
  assert.ok(Array.from({ length: LAYOUT_FAMILIES.length }, (_, index) => parserStructureVariantForSample(index, "train")).every((value) => value === "complete"));
});

test("product-only and numeric recipes alter document truth rather than parser postprocessing", () => {
  const productRandom = rng(41);
  const productBase = semanticLayout(10, productRandom, "classic_medication_bag");
  const productOnly = applyParserStructureVariant(productBase, { index: 10, split: "train", splitOrdinal: 4, random: productRandom });
  assert.equal(productOnly.parser_structure_variant, "product_only");
  const groups = [...new Set(productOnly.regions.filter((region) => region.semantic_role === "product").map((region) => region.association_group))];
  assert.ok(groups.some((group) => {
    const roles = new Set(productOnly.regions.filter((region) => region.association_group === group).map((region) => region.semantic_role));
    return roles.has("product") && !roles.has("dose") && !roles.has("frequency") && !roles.has("duration") && !roles.has("instruction") && !roles.has("schedule");
  }));

  const scheduleRandom = rng(45);
  const scheduleBase = semanticLayout(8, scheduleRandom, "legacy_preprinted_medication_bag");
  const scheduleProductOnly = applyParserStructureVariant(scheduleBase, { index: 8, split: "train", splitOrdinal: 4, random: scheduleRandom });
  assert.equal(scheduleProductOnly.parser_structure_variant, "product_only");
  assert.equal(scheduleProductOnly.regions.some((region) => region.semantic_role === "schedule" && region.association_group !== "document"), false);

  const numericRandom = rng(43);
  const numericBase = semanticLayout(13, numericRandom, "prescription_table");
  const numeric = applyParserStructureVariant(numericBase, { index: 13, split: "train", splitOrdinal: 7, random: numericRandom });
  assert.equal(numeric.parser_structure_variant, "numeric_cells");
  assert.ok(numeric.regions.filter((region) => ["dose", "frequency", "duration"].includes(region.semantic_role)).every((region) => /^\d+(?:\.\d+)?$/.test(region.text)));
});

test("ambiguous spacing stays association-hard without literal medication glyph collisions", () => {
  const random = () => 0.99;
  const base = semanticLayout(7, random, "pharmacy_guide_receipt_sidecar");
  assert.ok(new Set(base.regions.filter((region) => region.semantic_role === "product").map((region) => region.association_group)).size >= 2);
  const stressed = applyParserStructureVariant(base, { index: 7, split: "train", splitOrdinal: 9, random });
  assert.equal(stressed.parser_structure_variant, "ambiguous_spacing");

  const medication = stressed.regions.filter((region) => region.association_group !== "document" && [
    "product", "dose", "frequency", "duration", "instruction",
  ].includes(region.semantic_role));
  for (let left = 0; left < medication.length; left += 1) {
    for (let right = left + 1; right < medication.length; right += 1) {
      if (medication[left].association_group === medication[right].association_group) continue;
      assert.equal(
        boxOverlap(medication[left].natural_text_box, medication[right].natural_text_box),
        false,
        `${medication[left].region_id} overlaps ${medication[right].region_id}`,
      );
    }
  }
});

test("sidecar no-header stress removes the saturated header band with the text", () => {
  const random = rng(61);
  const base = semanticLayout(7, random, "pharmacy_guide_receipt_sidecar");
  assert.match(base.decorations, /class="guide-header-band"/u);
  const stressed = applyParserStructureVariant(base, { index: 7, split: "train", splitOrdinal: 6, random });
  assert.equal(stressed.parser_structure_variant, "no_headers");
  assert.equal(stressed.regions.some((region) => region.semantic_role === "header"), false);
  assert.doesNotMatch(stressed.decorations, /class="guide-header-band"/u);
  assert.match(stressed.decorations, /class="guide-header-band-empty"/u);
});

test("held-out recipes include fraction/partial-header and header-only negatives", () => {
  const fractionRandom = rng(47);
  const fractionBase = semanticLayout(15, fractionRandom, "compact_prescription_form");
  const fraction = applyParserStructureVariant(fractionBase, { index: 15, split: "val", splitOrdinal: 1, random: fractionRandom });
  assert.equal(fraction.parser_structure_variant, "fraction_dose_partial_headers");
  assert.ok(fraction.regions.some((region) => region.semantic_role === "dose" && region.text === "1/2정"));
  const fractionRows = expectedRows({ regions: fraction.regions });
  assert.ok(fractionRows.some((row) => row.draft.dose_amount === 0.5));

  const negativeRandom = rng(53);
  const negativeBase = semanticLayout(25, negativeRandom, "pharmacy_information_sheet");
  const negative = applyParserStructureVariant(negativeBase, { index: 25, split: "val", splitOrdinal: 2, random: negativeRandom });
  assert.equal(negative.parser_structure_variant, "header_only_negative");
  assert.equal(negative.regions.some((region) => ["product", "product_label", "dose", "frequency", "duration", "instruction", "schedule"].includes(region.semantic_role) && region.association_group !== "document"), false);

  const scheduleNegativeRandom = rng(55);
  const scheduleNegativeBase = semanticLayout(8, scheduleNegativeRandom, "legacy_preprinted_medication_bag");
  const scheduleNegative = applyParserStructureVariant(scheduleNegativeBase, { index: 8, split: "val", splitOrdinal: 2, random: scheduleNegativeRandom });
  assert.equal(scheduleNegative.regions.some((region) => region.semantic_role === "schedule" && region.association_group !== "document"), false);
});

test("parser gold derives supported structured semantics from associated instructions", () => {
  const rows = parserTrainingRows({
    regions: [
      { region_id: "p", text: "가나다정", semantic_role: "product", association_group: "m1" },
      { region_id: "i", text: "오전 8시 식후 필요시 경구 복용", semantic_role: "instruction", association_group: "m1" },
    ],
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].draft.schedule_times, undefined);
  assert.equal(rows[0].draft.meal_relation, "after_meal");
  assert.equal(rows[0].draft.as_needed, true);
  assert.equal(rows[0].draft.administration_route, "oral");
  assert.equal(rows[0].evidence.schedule_times, undefined);
  assert.deepEqual(rows[0].evidence.meal_relation, ["i"]);
  assert.deepEqual(rows[0].evidence.as_needed, ["i"]);
  assert.deepEqual(rows[0].evidence.administration_route, ["i"]);

  const scheduledRows = parserTrainingRows({
    regions: [
      { region_id: "p2", text: "라마바정", semantic_role: "product", association_group: "m2" },
      { region_id: "i2", text: "오전 8시 식후 경구 복용", semantic_role: "instruction", association_group: "m2" },
    ],
  });
  assert.deepEqual(scheduledRows[0].draft.schedule_times, ["08:00"]);
  assert.deepEqual(scheduledRows[0].evidence.schedule_times, ["i2"]);

  const splitPrnRows = parserTrainingRows({
    regions: [
      { region_id: "p3", text: "사아자정", semantic_role: "product", association_group: "m3" },
      { region_id: "prn3", text: "필요시 복용", semantic_role: "instruction", association_group: "m3" },
      { region_id: "schedule3", text: "오전 8시", semantic_role: "schedule", association_group: "m3" },
      { region_id: "frequency3", text: "1회", semantic_role: "frequency", association_group: "m3" },
    ],
  });
  assert.equal(splitPrnRows[0].draft.as_needed, true);
  assert.equal(splitPrnRows[0].draft.schedule_times, undefined);
  assert.equal(splitPrnRows[0].draft.frequency_per_day, undefined);
  assert.equal(splitPrnRows[0].evidence.schedule_times, undefined);
  assert.equal(splitPrnRows[0].evidence.frequency_per_day, undefined);

  const legacyRows = expectedRows({
    regions: [
      { region_id: "p", text: "가나다정", semantic_role: "product", association_group: "m1" },
      { region_id: "i", text: "오전 8시 식후 필요시 경구 복용", semantic_role: "instruction", association_group: "m1" },
    ],
  });
  assert.deepEqual(legacyRows[0].draft, {});
});
