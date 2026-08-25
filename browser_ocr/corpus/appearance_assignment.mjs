import { appearanceForIndex } from "../detection/synthetic_appearance.mjs";
import { PARSER_STRUCTURE_VARIANTS } from "./parser_structure.mjs";

const VARIANT_PHASE_STRIDE = 13;

function splitVariants(split) {
  const variants = PARSER_STRUCTURE_VARIANTS[split];
  if (!variants) throw new Error(`unsupported document split: ${split}`);
  return variants;
}

export function appearanceCycleIndexForSplit(split, splitOrdinal) {
  if (!Number.isInteger(splitOrdinal) || splitOrdinal < 0) {
    throw new Error("split ordinal must be a non-negative integer");
  }
  const variants = splitVariants(split);
  const variantSlot = splitOrdinal % variants.length;
  const repetition = Math.floor(splitOrdinal / variants.length);
  // Transpose the split-local parser-variant cycle before feeding the appearance
  // cycle. Each held-out structure variant therefore advances through the full
  // visual catalog over repeated documents instead of inheriting one raw-index
  // residue class from the 8:1:1 document split.
  return repetition + variantSlot * VARIANT_PHASE_STRIDE;
}

export function appearanceForSplitOrdinal(split, splitOrdinal) {
  return appearanceForIndex(appearanceCycleIndexForSplit(split, splitOrdinal));
}