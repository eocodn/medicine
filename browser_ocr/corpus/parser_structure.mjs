import { DOCUMENT_HEIGHT, DOCUMENT_WIDTH, estimateRenderedTextBox } from "../detection/synthetic_layouts.mjs";

export const PARSER_STRUCTURE_REVISION = 1;

const TRAIN_VARIANTS = [
  "complete",
  "drop_dose",
  "drop_frequency",
  "drop_duration",
  "product_only",
  "partial_headers",
  "no_headers",
  "numeric_cells",
  "regimen_distractor",
  "ambiguous_spacing",
];
const VAL_VARIANTS = [
  "short_headers_product_only",
  "fraction_dose_partial_headers",
  "header_only_negative",
];
const TEST_VARIANTS = [
  "fraction_dose_no_headers",
  "numeric_product_only",
  "ambiguous_spacing_short_headers",
];

export const PARSER_STRUCTURE_VARIANTS = Object.freeze({
  train: [...TRAIN_VARIANTS],
  val: [...VAL_VARIANTS],
  test: [...TEST_VARIANTS],
});

function quad(x, y, width, height) {
  return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]];
}

function replaceText(region, text) {
  const [x, y] = region.text_origin;
  const rendered = estimateRenderedTextBox(text, region.font_size_px);
  const paddingX = Math.round(region.font_size_px * 0.45);
  const paddingY = Math.round(region.font_size_px * 0.55);
  const left = Math.max(0, x - paddingX);
  const top = Math.max(0, y - paddingY);
  const right = Math.min(DOCUMENT_WIDTH - 1, x + rendered.width + paddingX);
  const bottom = Math.min(DOCUMENT_HEIGHT - 1, y + rendered.height + paddingY);
  return {
    ...region,
    text,
    polygon: quad(left, top, right - left, bottom - top),
    natural_text_box: quad(x, y, rendered.width, rendered.height),
  };
}

function moveRegion(region, dx, dy, suffix = "") {
  const translate = (polygon) => polygon.map(([x, y]) => [
    Math.max(0, Math.min(DOCUMENT_WIDTH - 1, x + dx)),
    Math.max(0, Math.min(DOCUMENT_HEIGHT - 1, y + dy)),
  ]);
  return {
    ...region,
    region_id: `${region.region_id}${suffix}`,
    polygon: translate(region.polygon),
    natural_text_box: translate(region.natural_text_box),
    layout_slot: translate(region.layout_slot),
    text_origin: [
      Math.max(0, Math.min(DOCUMENT_WIDTH - 1, region.text_origin[0] + dx)),
      Math.max(0, Math.min(DOCUMENT_HEIGHT - 1, region.text_origin[1] + dy)),
    ],
  };
}

function medicationGroups(regions) {
  return [...new Set(regions
    .filter((region) => region.semantic_role === "product" && region.association_group !== "document")
    .map((region) => region.association_group))];
}

function targetGroup(regions, random) {
  const groups = medicationGroups(regions);
  return groups.length ? groups[Math.floor(random() * groups.length)] : null;
}

function dropRoleForOneGroup(regions, role, random) {
  const group = targetGroup(regions, random);
  if (!group) return regions;
  return regions.filter((region) => !(region.association_group === group && region.semantic_role === role));
}

function productOnlyOneGroup(regions, random) {
  const group = targetGroup(regions, random);
  if (!group) return regions;
  return regions.filter((region) => !(
    region.association_group === group
    && ["dose", "frequency", "duration", "instruction"].includes(region.semantic_role)
  ));
}

function partialHeaders(regions) {
  let headerIndex = 0;
  return regions.filter((region) => {
    if (region.semantic_role !== "header") return true;
    const keep = headerIndex % 2 === 0;
    headerIndex += 1;
    return keep;
  });
}

function shortHeaders(regions) {
  return regions.map((region) => {
    if (region.semantic_role !== "header") return region;
    const compact = region.text.replace(/\s+/g, "");
    if (/약품명|제품명|의약품명|약명/.test(compact)) return replaceText(region, "약품명");
    if (/1회|투약량|용량/.test(compact)) return replaceText(region, "용량");
    if (/횟수|회수|1일/.test(compact)) return replaceText(region, "횟수");
    if (/일수|투약일|총.*일/.test(compact)) return replaceText(region, "일수");
    return region;
  });
}

function numericCells(regions) {
  return regions.map((region) => {
    if (!["dose", "frequency", "duration"].includes(region.semantic_role)) return region;
    const match = region.text.match(/\d+(?:\.\d+)?/);
    return match ? replaceText(region, match[0]) : region;
  });
}

function fractionDose(regions, random) {
  const group = targetGroup(regions, random);
  let replaced = false;
  return regions.map((region) => {
    if (!replaced && region.association_group === group && region.semantic_role === "dose") {
      replaced = true;
      return replaceText(region, "1/2정");
    }
    return region;
  });
}

function addRegimenDistractor(regions) {
  const source = regions.find((region) => ["dose", "frequency", "duration"].includes(region.semantic_role));
  if (!source) return regions;
  const moved = moveRegion(source, 0, Math.min(420, DOCUMENT_HEIGHT - source.polygon[2][1] - 40), "-parser-distractor");
  return [...regions, {
    ...moved,
    critical: false,
    association_group: "document",
    semantic_role: "label",
    region_class: "distractor",
  }];
}

function ambiguousSpacing(regions) {
  const groups = medicationGroups(regions);
  if (groups.length < 2) return regions;
  const first = groups[0];
  const productY = new Map(groups.map((group) => {
    const product = regions.find((region) => region.association_group === group && region.semantic_role === "product");
    return [group, product ? product.text_origin[1] : null];
  }));
  const y1 = productY.get(groups[0]);
  const y2 = productY.get(groups[1]);
  if (!Number.isFinite(y1) || !Number.isFinite(y2)) return regions;
  const targetY = (y1 + y2) / 2;
  return regions.map((region) => {
    if (region.association_group !== first || !["dose", "frequency", "duration"].includes(region.semantic_role)) return region;
    return moveRegion(region, 0, targetY - region.text_origin[1]);
  });
}

function headerOnly(regions) {
  return regions.filter((region) => !(
    ["product", "product_label", "dose", "frequency", "duration", "instruction"].includes(region.semantic_role)
    && region.association_group !== "document"
  ));
}

export function parserStructureVariantForSample(index, split) {
  const pools = { train: TRAIN_VARIANTS, val: VAL_VARIANTS, test: TEST_VARIANTS };
  const pool = pools[split];
  if (!pool) throw new Error(`unsupported parser structure split: ${split}`);
  if (split === "train") return pool[index % pool.length];
  const cycle = Math.floor(index / 10);
  return pool[cycle % pool.length];
}

export function applyParserStructureVariant(layout, { index, split, random }) {
  const variant = parserStructureVariantForSample(index, split);
  let regions = layout.regions.map((region) => structuredClone(region));
  if (variant === "drop_dose") regions = dropRoleForOneGroup(regions, "dose", random);
  else if (variant === "drop_frequency") regions = dropRoleForOneGroup(regions, "frequency", random);
  else if (variant === "drop_duration") regions = dropRoleForOneGroup(regions, "duration", random);
  else if (variant === "product_only") regions = productOnlyOneGroup(regions, random);
  else if (variant === "partial_headers") regions = partialHeaders(regions);
  else if (variant === "no_headers") regions = regions.filter((region) => region.semantic_role !== "header");
  else if (variant === "numeric_cells") regions = numericCells(regions);
  else if (variant === "regimen_distractor") regions = addRegimenDistractor(regions);
  else if (variant === "ambiguous_spacing") regions = ambiguousSpacing(regions);
  else if (variant === "short_headers_product_only") regions = productOnlyOneGroup(shortHeaders(regions), random);
  else if (variant === "fraction_dose_partial_headers") regions = partialHeaders(fractionDose(regions, random));
  else if (variant === "header_only_negative") regions = headerOnly(regions);
  else if (variant === "fraction_dose_no_headers") regions = fractionDose(regions.filter((region) => region.semantic_role !== "header"), random);
  else if (variant === "numeric_product_only") regions = productOnlyOneGroup(numericCells(regions), random);
  else if (variant === "ambiguous_spacing_short_headers") regions = shortHeaders(ambiguousSpacing(regions));
  else if (variant !== "complete") throw new Error(`unsupported parser structure variant: ${variant}`);

  return {
    ...layout,
    regions,
    parser_structure_variant: variant,
    scenario_tags: [...new Set([...layout.scenario_tags, `parser_structure_${variant}`])],
    risk_tags: [...new Set([
      ...layout.risk_tags,
      ...(variant === "complete" ? [] : ["parser_structure_variation"]),
      ...(variant.includes("header") ? ["header_robustness"] : []),
      ...(variant.includes("product_only") ? ["partial_medication_row"] : []),
      ...(variant.includes("numeric") || variant.includes("fraction") ? ["contextual_numeric_semantics"] : []),
      ...(variant.includes("ambiguous") || variant.includes("distractor") ? ["association_hard_negative"] : []),
    ])],
  };
}
