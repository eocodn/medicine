export const DOCUMENT_WIDTH = 1280;
export const DOCUMENT_HEIGHT = 1600;

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function quad(x, y, width, height) {
  return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]];
}

function glyphWidthEm(character) {
  if (/\s/u.test(character)) return 0.28;
  if (/[\u1100-\u11ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/u.test(character)) return 1;
  if (/[○□◇△▽※]/u.test(character)) return 1;
  if (/[0-9]/u.test(character)) return 0.56;
  if (/[A-Z]/u.test(character)) return 0.65;
  if (/[a-z]/u.test(character)) return 0.55;
  if (/[.,:;\-_/·]/u.test(character)) return 0.34;
  if (/[()[\]{}]/u.test(character)) return 0.42;
  return 0.72;
}

export function estimateRenderedTextBox(text, fontSize) {
  const widthEm = [...String(text)].reduce((sum, character) => sum + glyphWidthEm(character), 0);
  return {
    width: Math.max(1, Math.round(widthEm * fontSize)),
    height: Math.max(1, Math.round(fontSize * 1.08)),
  };
}

export function region(regionId, text, x, y, width, height, {
  critical = false,
  associationGroup = "document",
  semanticRole = "context",
  regionClass = "context",
  fontSize = 38,
  textFill = null,
} = {}) {
  const rendered = estimateRenderedTextBox(text, fontSize);
  const paddingX = Math.round(fontSize * 0.45);
  const paddingY = Math.round(fontSize * 0.55);
  const left = Math.max(0, x - paddingX);
  const top = Math.max(0, y - paddingY);
  const right = Math.min(DOCUMENT_WIDTH - 1, x + rendered.width + paddingX);
  const bottom = Math.min(DOCUMENT_HEIGHT - 1, y + rendered.height + paddingY);
  return {
    region_id: regionId,
    text,
    polygon: quad(left, top, right - left, bottom - top),
    text_origin: [x, y],
    natural_text_box: quad(x, y, rendered.width, rendered.height),
    layout_slot: quad(x, y, width, height),
    critical,
    association_group: associationGroup,
    semantic_role: semanticRole,
    region_class: regionClass,
    font_size_px: fontSize,
    ...(textFill ? { text_fill: textFill } : {}),
  };
}

export function scaledFont(random, base, spread = 0.12, minimum = 20) {
  return Math.max(minimum, Math.round(base * (1 - spread + random() * spread * 2)));
}

export function fittedFontSize(text, preferred, maximumWidth, minimum = 12) {
  let fontSize = preferred;
  while (fontSize > minimum && estimateRenderedTextBox(text, fontSize).width > maximumWidth) fontSize -= 1;
  if (estimateRenderedTextBox(text, fontSize).width > maximumWidth) {
    throw new Error(`text cannot fit layout slot at minimum font size: ${text}`);
  }
  return fontSize;
}

export function jitteredColumns(random, bases, magnitude) {
  return Object.fromEntries(Object.entries(bases).map(([key, value]) => [
    key,
    Math.round(value + (random() * 2 - 1) * magnitude),
  ]));
}

export function renderLayoutRegions(regions, printer = { profile: "laser_clean" }) {
  const fontFamily = printer.profile === "ink_bleed"
    ? "Noto Serif CJK KR, serif"
    : "Noto Sans CJK KR, sans-serif";
  const fill = printer.profile === "low_toner" ? "#666" : "#202020";
  return regions.map((item) => {
    const [x, y] = item.text_origin || item.polygon[0];
    const baseline = y + Math.round(item.font_size_px * 0.82);
    const itemFill = item.text_fill || fill;
    const primary = `<text x="${x}" y="${baseline}" font-family="${fontFamily}" font-size="${item.font_size_px}" fill="${itemFill}">${escapeXml(item.text)}</text>`;
    if (printer.profile !== "ink_bleed") return primary;
    return `${primary}\n<text x="${x + 1.2}" y="${baseline + 0.7}" font-family="${fontFamily}" font-size="${item.font_size_px}" fill="${itemFill}" opacity="0.24">${escapeXml(item.text)}</text>`;
  }).join("\n");
}