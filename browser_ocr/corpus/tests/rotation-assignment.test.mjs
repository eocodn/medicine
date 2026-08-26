import assert from "node:assert/strict";
import test from "node:test";

import { rotationCycleIndexForSplit } from "../appearance_assignment.mjs";
import { PARSER_STRUCTURE_VARIANTS } from "../parser_structure.mjs";
import { pageRotationForSample } from "../../detection/synthetic_capture.mjs";
import { PAGE_ROTATIONS } from "../../detection/synthetic_catalog.mjs";

const EXPECTED_COUNTS = Object.freeze({ train: 3360, val: 420, test: 420 });

test("every parser structure variant independently covers every page rotation", () => {
  for (const split of Object.keys(EXPECTED_COUNTS)) {
    const variants = PARSER_STRUCTURE_VARIANTS[split];
    const rotationsByVariant = new Map(variants.map((variant) => [variant, new Set()]));
    for (let ordinal = 0; ordinal < EXPECTED_COUNTS[split]; ordinal += 1) {
      const variant = variants[ordinal % variants.length];
      const rotationIndex = rotationCycleIndexForSplit(split, ordinal);
      rotationsByVariant.get(variant).add(pageRotationForSample(0, 0, rotationIndex));
    }
    for (const rotations of rotationsByVariant.values()) {
      assert.deepEqual(rotations, new Set(PAGE_ROTATIONS));
    }
  }
});

test("rotation assignment rejects invalid split ordinals", () => {
  assert.throws(() => rotationCycleIndexForSplit("unknown", 0), /unsupported document split/);
  assert.throws(() => rotationCycleIndexForSplit("train", -1), /non-negative integer/);
  assert.throws(() => rotationCycleIndexForSplit("train", 1.5), /non-negative integer/);
});