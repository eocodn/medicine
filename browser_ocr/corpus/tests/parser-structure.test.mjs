import assert from "node:assert/strict";
import test from "node:test";

import { buildLayout } from "../../detection/synthetic_layouts.mjs";
import { expectedRows } from "../parser_truth.mjs";
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

test("parser structure recipes are split-specific and train covers its recipe pool", () => {
  const train = new Set(PARSER_STRUCTURE_VARIANTS.train);
  const val = new Set(PARSER_STRUCTURE_VARIANTS.val);
  const heldout = new Set(PARSER_STRUCTURE_VARIANTS.test);
  assert.equal([...train].some((value) => val.has(value) || heldout.has(value)), false);
  assert.equal([...val].some((value) => heldout.has(value)), false);
  assert.deepEqual(
    new Set(Array.from(
      { length: PARSER_STRUCTURE_VARIANTS.train.length },
      (_, offset) => parserStructureVariantForSample(offset + 6, "train"),
    )),
    train,
  );
  assert.ok(Array.from({ length: 6 }, (_, index) => parserStructureVariantForSample(index, "train")).every((value) => value === "complete"));
});

test("product-only and numeric recipes alter document truth rather than parser postprocessing", () => {
  const productRandom = rng(41);
  const productBase = buildLayout(10, productRandom, { products: products() });
  const productOnly = applyParserStructureVariant(productBase, { index: 10, split: "train", splitOrdinal: 4, random: productRandom });
  assert.equal(productOnly.parser_structure_variant, "product_only");
  const groups = [...new Set(productOnly.regions.filter((region) => region.semantic_role === "product").map((region) => region.association_group))];
  assert.ok(groups.some((group) => {
    const roles = new Set(productOnly.regions.filter((region) => region.association_group === group).map((region) => region.semantic_role));
    return roles.has("product") && !roles.has("dose") && !roles.has("frequency") && !roles.has("duration");
  }));

  const numericRandom = rng(43);
  const numericBase = buildLayout(13, numericRandom, { products: products() });
  const numeric = applyParserStructureVariant(numericBase, { index: 13, split: "train", splitOrdinal: 7, random: numericRandom });
  assert.equal(numeric.parser_structure_variant, "numeric_cells");
  assert.ok(numeric.regions.filter((region) => ["dose", "frequency", "duration"].includes(region.semantic_role)).every((region) => /^\d+(?:\.\d+)?$/.test(region.text)));
});

test("held-out recipes include fraction/partial-header and header-only negatives", () => {
  const fractionRandom = rng(47);
  const fractionBase = buildLayout(15, fractionRandom, { products: products() });
  const fraction = applyParserStructureVariant(fractionBase, { index: 15, split: "val", splitOrdinal: 1, random: fractionRandom });
  assert.equal(fraction.parser_structure_variant, "fraction_dose_partial_headers");
  assert.ok(fraction.regions.some((region) => region.semantic_role === "dose" && region.text === "1/2정"));
  const fractionRows = expectedRows({ regions: fraction.regions });
  assert.ok(fractionRows.some((row) => row.draft.dose_amount === 0.5));

  const negativeRandom = rng(53);
  const negativeBase = buildLayout(25, negativeRandom, { products: products() });
  const negative = applyParserStructureVariant(negativeBase, { index: 25, split: "val", splitOrdinal: 2, random: negativeRandom });
  assert.equal(negative.parser_structure_variant, "header_only_negative");
  assert.equal(negative.regions.some((region) => ["product", "product_label", "dose", "frequency", "duration"].includes(region.semantic_role) && region.association_group !== "document"), false);
});
