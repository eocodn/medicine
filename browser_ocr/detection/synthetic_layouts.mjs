import { LAYOUT_FAMILIES } from "./synthetic_catalog.mjs";
import { buildLayoutFamily } from "./synthetic_layout_families.mjs";

export {
  DOCUMENT_HEIGHT,
  DOCUMENT_WIDTH,
  estimateRenderedTextBox,
  renderLayoutRegions,
} from "./synthetic_layout_primitives.mjs";

export function buildLayout(index, random, { document } = {}) {
  if (!Number.isInteger(index) || index < 0) throw new Error("layout index must be a non-negative integer");
  if (typeof random !== "function") throw new Error("layout random source must be a function");
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new Error("layout document truth must be an object");
  }
  if (!LAYOUT_FAMILIES.includes(document.layout_family)) {
    throw new Error(`unsupported layout family: ${document.layout_family}`);
  }
  if (!Array.isArray(document.medications) || document.medications.length === 0) {
    throw new Error("layout document medications must be non-empty");
  }
  return buildLayoutFamily(index, random, document);
}