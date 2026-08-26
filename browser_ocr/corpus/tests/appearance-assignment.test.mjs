import assert from "node:assert/strict";
import test from "node:test";

import { appearanceForSplitOrdinal } from "../appearance_assignment.mjs";
import { PARSER_STRUCTURE_VARIANTS } from "../parser_structure.mjs";
import {
  BACKGROUND_PROFILES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
  SCENE_PROP_PROFILES,
} from "../../detection/synthetic_catalog.mjs";

const EXPECTED_COUNTS = Object.freeze({ train: 3360, val: 420, test: 420 });

function valuesForSplit(split) {
  return Array.from({ length: EXPECTED_COUNTS[split] }, (_, ordinal) => (
    appearanceForSplitOrdinal(split, ordinal)
  ));
}

function assertFullCoverage(values, key, expected) {
  assert.deepEqual(new Set(values.map((value) => value[key])), new Set(expected));
}

test("every document split independently covers the full appearance catalog", () => {
  for (const split of Object.keys(EXPECTED_COUNTS)) {
    const values = valuesForSplit(split);
    assertFullCoverage(values, "material_profile", MATERIAL_PROFILES);
    assertFullCoverage(values, "printer_profile", PRINTER_PROFILES);
    assertFullCoverage(values, "background_profile", BACKGROUND_PROFILES);
    assertFullCoverage(values, "scene_prop_profile", SCENE_PROP_PROFILES);
  }
});

test("appearance strata are not pinned to parser structure variants", () => {
  for (const split of Object.keys(EXPECTED_COUNTS)) {
    const variants = PARSER_STRUCTURE_VARIANTS[split];
    const byVariant = new Map(variants.map((variant) => [variant, []]));
    const appearances = valuesForSplit(split);
    appearances.forEach((appearance, ordinal) => {
      byVariant.get(variants[ordinal % variants.length]).push(appearance);
    });
    for (const appearancesForVariant of byVariant.values()) {
      assertFullCoverage(appearancesForVariant, "material_profile", MATERIAL_PROFILES);
      assertFullCoverage(appearancesForVariant, "background_profile", BACKGROUND_PROFILES);
      assertFullCoverage(appearancesForVariant, "scene_prop_profile", SCENE_PROP_PROFILES);
    }
  }
});

test("appearance assignment rejects invalid split ordinals", () => {
  assert.throws(() => appearanceForSplitOrdinal("unknown", 0), /unsupported document split/);
  assert.throws(() => appearanceForSplitOrdinal("train", -1), /non-negative integer/);
  assert.throws(() => appearanceForSplitOrdinal("train", 1.5), /non-negative integer/);
});