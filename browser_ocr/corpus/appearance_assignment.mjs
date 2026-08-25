import { appearanceForIndex } from "../detection/synthetic_appearance.mjs";
import { PARSER_STRUCTURE_VARIANTS } from "./parser_structure.mjs";

const APPEARANCE_VARIANT_PHASE_STRIDE = 13;
const ROTATION_VARIANT_PHASE_STRIDE = 17;

function splitVariants(split) {
  const variants = PARSER_STRUCTURE_VARIANTS[split];
  if (!variants) throw new Error(`unsupported document split: ${split}`);
  return variants;
}

function cycleIndexForSplit(split, splitOrdinal, phaseStride) {
  if (!Number.isInteger(splitOrdinal) || splitOrdinal < 0) {
    throw new Error("split ordinal must be a non-negative integer");
  }
  const variants = splitVariants(split);
  const variantSlot = splitOrdinal % variants.length;
  const repetition = Math.floor(splitOrdinal / variants.length);
  return repetition + variantSlot * phaseStride;
}

export function appearanceCycleIndexForSplit(split, splitOrdinal) {
  // Transpose the split-local parser-variant cycle before feeding the appearance
  // cycle. Each held-out structure variant therefore advances through the full
  // visual catalog over repeated documents instead of inheriting one raw-index
  // residue class from the 8:1:1 document split.
  return cycleIndexForSplit(split, splitOrdinal, APPEARANCE_VARIANT_PHASE_STRIDE);
}

export function rotationCycleIndexForSplit(split, splitOrdinal) {
  // Rotation needs an independent phase from appearance, but the same
  // split-local transpose prevents a parser structure variant from becoming a
  // proxy for one raw sample-index rotation residue.
  return cycleIndexForSplit(split, splitOrdinal, ROTATION_VARIANT_PHASE_STRIDE);
}

export function appearanceForSplitOrdinal(split, splitOrdinal) {
  return appearanceForIndex(appearanceCycleIndexForSplit(split, splitOrdinal));
}