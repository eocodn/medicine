import {
  fittedFontSize,
  region,
  scaledFont,
} from "./synthetic_layout_primitives.mjs";

const STYLES = Object.freeze([
  Object.freeze({
    id: "yellow_integrated",
    accent: "#d1a91f",
    accentSoft: "#f4e8a7",
    paper: "#f7f4ea",
    receipt: "#f2efe5",
    border: "#d1c79a",
    headerText: "#2b2a23",
    receiptX: 995,
    rowStart: 330,
    rowGap: 108,
    detachedReceipt: false,
    zebra: false,
    stamp: false,
  }),
  Object.freeze({
    id: "blue_striped",
    accent: "#23436f",
    accentSoft: "#d9e6ef",
    paper: "#f7f8f7",
    receipt: "#f4f6f7",
    border: "#8798ab",
    headerText: "#f7f9fb",
    receiptX: 990,
    rowStart: 350,
    rowGap: 112,
    detachedReceipt: false,
    zebra: true,
    stamp: false,
  }),
  Object.freeze({
    id: "cream_dense_receipt",
    accent: "#c0aa4f",
    accentSoft: "#eee7c9",
    paper: "#f4f0e5",
    receipt: "#efebdf",
    border: "#aaa38c",
    headerText: "#2b2a23",
    receiptX: 985,
    rowStart: 335,
    rowGap: 104,
    detachedReceipt: false,
    zebra: false,
    stamp: false,
  }),
  Object.freeze({
    id: "navy_dense_guide",
    accent: "#18345f",
    accentSoft: "#dbe2eb",
    paper: "#f4f4f1",
    receipt: "#f1f1ee",
    border: "#718099",
    headerText: "#f7f9fb",
    receiptX: 1005,
    rowStart: 325,
    rowGap: 102,
    detachedReceipt: true,
    zebra: false,
    stamp: true,
  }),
  Object.freeze({
    id: "low_contrast_blue",
    accent: "#6b86a2",
    accentSoft: "#e3e8eb",
    paper: "#f3f2ed",
    receipt: "#eef0ef",
    border: "#9ea9b0",
    headerText: "#263747",
    receiptX: 995,
    rowStart: 345,
    rowGap: 118,
    detachedReceipt: false,
    zebra: true,
    stamp: false,
  }),
]);

export const PHARMACY_GUIDE_STYLE_IDS = Object.freeze(STYLES.map((style) => style.id));

export function pharmacyGuideStyleForIndex(index) {
  if (!Number.isInteger(index) || index < 0) throw new Error("pharmacy-guide style index must be a non-negative integer");
  return STYLES[index % STYLES.length];
}

function medicationColumns(style) {
  const mainRight = style.receiptX - 18;
  return {
    image: 58,
    product: 190,
    dose: 515,
    frequency: 615,
    duration: 700,
    instruction: 785,
    mainRight,
  };
}

function qrDistractor(index, x, y, module = 7) {
  const cells = 13;
  const squares = [];
  const finder = (cx, cy) => `<g fill="#1d1d1d">
<rect x="${x + cx * module}" y="${y + cy * module}" width="${module * 5}" height="${module * 5}"/>
<rect x="${x + (cx + 1) * module}" y="${y + (cy + 1) * module}" width="${module * 3}" height="${module * 3}" fill="#f4f4f1"/>
<rect x="${x + (cx + 2) * module}" y="${y + (cy + 2) * module}" width="${module}" height="${module}"/>
</g>`;
  for (let row = 0; row < cells; row += 1) {
    for (let column = 0; column < cells; column += 1) {
      const inFinder = (column < 5 && row < 5)
        || (column >= cells - 5 && row < 5)
        || (column < 5 && row >= cells - 5);
      if (inFinder) continue;
      const mixed = (Math.imul(index + 17, 1103515245) ^ Math.imul(row + 3, 2654435761) ^ Math.imul(column + 11, 2246822519)) >>> 0;
      if ((mixed & 3) === 0 || ((row + column + index) % 7 === 0)) {
        squares.push(`<rect x="${x + column * module}" y="${y + row * module}" width="${module}" height="${module}" fill="#202020"/>`);
      }
    }
  }
  return `<g class="qr-distractor" opacity="0.9">
<rect x="${x - 5}" y="${y - 5}" width="${cells * module + 10}" height="${cells * module + 10}" rx="5" fill="#fafaf8" stroke="#bbb"/>
${finder(0, 0)}${finder(cells - 5, 0)}${finder(0, cells - 5)}${squares.join("\n")}
</g>`;
}

function medicationThumbnail(index, row, style, x, y) {
  const shape = (index + row) % 4;
  const body = shape === 0
    ? `<ellipse cx="${x + 52}" cy="${y + 31}" rx="28" ry="19" fill="#d8d6d0" stroke="#aaa7a0"/><path d="M${x + 29} ${y + 31} h46" stroke="#b6b2aa"/>`
    : shape === 1
      ? `<rect x="${x + 22}" y="${y + 13}" width="62" height="36" rx="18" fill="#d9d8d3" stroke="#aaa7a0"/><path d="M${x + 53} ${y + 13} v36" stroke="#b0ada6"/>`
      : shape === 2
        ? `<ellipse cx="${x + 39}" cy="${y + 31}" rx="20" ry="16" fill="#d8d7d2" stroke="#aaa7a0"/><ellipse cx="${x + 71}" cy="${y + 31}" rx="20" ry="16" fill="#ece9df" stroke="#b5b0a5"/>`
        : `<rect x="${x + 24}" y="${y + 16}" width="58" height="30" rx="15" fill="#d5d5d1" stroke="#a9a9a3" transform="rotate(-8 ${x + 53} ${y + 31})"/>`;
  return `<g class="medication-thumbnail">
<rect x="${x}" y="${y}" width="106" height="64" rx="2" fill="#e2e0da" stroke="${style.border}" opacity="0.9"/>
<path d="M${x + 3} ${y + 10} H${x + 103} M${x + 3} ${y + 45} H${x + 103}" stroke="#fff" opacity="0.3"/>
${body}
<ellipse cx="${x + 42}" cy="${y + 22}" rx="9" ry="5" fill="#fff" opacity="0.38"/>
</g>`;
}

function receiptRegions(document, style) {
  const x = style.receiptX + 14;
  const valueX = style.receiptX + 148;
  const rowStart = 198;
  const rowGap = 48;
  const regions = [
    region("receipt-title", "약제비 계산서·영수증", x, 68, 232, 31, { semanticRole: "receipt", regionClass: "distractor", fontSize: 19 }),
    region("receipt-patient", `성명 ${document.context.patient}`, x, 112, 220, 26, { semanticRole: "receipt", regionClass: "distractor", fontSize: 15 }),
    region("receipt-pharmacy", document.context.pharmacy, x, 148, 220, 26, { semanticRole: "pharmacy", regionClass: "distractor", fontSize: 15 }),
  ];
  document.receipt.entries.forEach((entry, row) => {
    const y = rowStart + row * rowGap;
    regions.push(
      region(`receipt-${entry.id}-label`, entry.label, x, y, 120, 24, { semanticRole: "receipt", regionClass: "distractor", fontSize: 14 }),
      region(`receipt-${entry.id}-value`, entry.value, valueX, y, 96, 24, { semanticRole: "receipt", regionClass: "distractor", fontSize: 14 }),
    );
  });
  const footerY = rowStart + document.receipt.entries.length * rowGap + 26;
  regions.push(
    region("receipt-legal-1", "본 영수증은 소득공제 확인용입니다.", x, footerY, 225, 22, { semanticRole: "receipt", regionClass: "distractor", fontSize: 12 }),
    region("receipt-legal-2", "문의사항은 조제약국에 확인하세요.", x, footerY + 27, 225, 22, { semanticRole: "receipt", regionClass: "distractor", fontSize: 12 }),
  );
  return { regions, rowStart, rowGap, footerY };
}

function medicationRegions(document, random, style) {
  const columns = medicationColumns(style);
  const rowFont = scaledFont(random, style.id === "low_contrast_blue" ? 21 : 23, 0.08, 18);
  const regions = [
    region("guide-image-label", "약품사진", columns.image, 270, 112, 28, { semanticRole: "header", fontSize: 17, textFill: style.headerText }),
    region("guide-product-label", "약품명", columns.product, 270, 300, 28, { semanticRole: "header", fontSize: 18, textFill: style.headerText }),
    region("guide-dose-label", "1회량", columns.dose, 270, 80, 28, { semanticRole: "header", fontSize: 17, textFill: style.headerText }),
    region("guide-frequency-label", "횟수", columns.frequency, 270, 70, 28, { semanticRole: "header", fontSize: 17, textFill: style.headerText }),
    region("guide-duration-label", "일수", columns.duration, 270, 70, 28, { semanticRole: "header", fontSize: 17, textFill: style.headerText }),
    region("guide-instruction-label", "복용방법", columns.instruction, 270, 160, 28, { semanticRole: "header", fontSize: 17, textFill: style.headerText }),
  ];
  document.medications.forEach((medication, row) => {
    const y = style.rowStart + row * style.rowGap;
    const productFont = fittedFontSize(medication.product, rowFont, columns.dose - columns.product - 24, 14);
    const instructionFont = fittedFontSize(medication.instruction_text, Math.max(16, rowFont - 3), columns.mainRight - columns.instruction - 8, 13);
    regions.push(
      region(`guide-product-${row}`, medication.product, columns.product, y, columns.dose - columns.product - 24, 40, {
        critical: true, associationGroup: medication.id, semanticRole: "product", regionClass: "medication", fontSize: productFont,
      }),
      region(`guide-dose-${row}`, medication.dose_text, columns.dose, y, 70, 40, {
        critical: true, associationGroup: medication.id, semanticRole: "dose", regionClass: "medication", fontSize: rowFont,
      }),
      region(`guide-frequency-${row}`, medication.frequency_text, columns.frequency, y, 60, 40, {
        critical: true, associationGroup: medication.id, semanticRole: "frequency", regionClass: "medication", fontSize: rowFont,
      }),
      region(`guide-duration-${row}`, medication.duration_text, columns.duration, y, 60, 40, {
        critical: true, associationGroup: medication.id, semanticRole: "duration", regionClass: "medication", fontSize: rowFont,
      }),
      region(`guide-instruction-${row}`, medication.instruction_text, columns.instruction, y, columns.mainRight - columns.instruction - 8, 40, {
        associationGroup: medication.id, semanticRole: "instruction", regionClass: "context", fontSize: instructionFont,
      }),
    );
  });
  return { regions, columns, rowFont };
}

function rowDecorations(index, document, style, columns) {
  return document.medications.map((_, row) => {
    const y = style.rowStart + row * style.rowGap - 19;
    const stripe = style.zebra && row % 2 === 1
      ? `<rect x="42" y="${y - 20}" width="${columns.mainRight - 42}" height="${style.rowGap - 2}" fill="${style.accentSoft}" opacity="0.58"/>`
      : "";
    return `${stripe}
${medicationThumbnail(index, row, style, columns.image, y - 6)}
<line x1="42" y1="${y + style.rowGap - 20}" x2="${columns.mainRight}" y2="${y + style.rowGap - 20}" stroke="${style.border}" stroke-width="1" opacity="0.72"/>`;
  }).join("\n");
}

function receiptDecorations(document, style, receipt) {
  const right = 1262;
  const lines = document.receipt.entries.map((_, row) => {
    const y = receipt.rowStart - 13 + row * receipt.rowGap;
    return `<line x1="${style.receiptX + 8}" y1="${y}" x2="${right}" y2="${y}" stroke="${style.border}" stroke-width="0.8"/>`;
  }).join("\n");
  const vertical = style.receiptX + 142;
  return `<g>
<line x1="${vertical}" y1="178" x2="${vertical}" y2="${receipt.footerY - 8}" stroke="${style.border}" stroke-width="0.8"/>
${lines}
</g>`;
}

function mainPaperSvg(style, mainRight) {
  if (style.detachedReceipt) {
    return `<rect x="26" y="24" width="${mainRight - 26}" height="1536" rx="8" fill="${style.paper}" stroke="${style.border}" stroke-width="1.5"/>
<rect x="${style.receiptX}" y="38" width="${1262 - style.receiptX}" height="1494" rx="5" fill="${style.receipt}" stroke="${style.border}" stroke-width="1.2"/>`;
  }
  return `<rect x="26" y="24" width="1236" height="1536" rx="7" fill="${style.paper}" stroke="${style.border}" stroke-width="1.5"/>
<rect x="${style.receiptX}" y="38" width="${1262 - style.receiptX}" height="1494" fill="${style.receipt}" stroke="${style.border}" stroke-width="0.8"/>`;
}

function stampDecoration(style) {
  if (!style.stamp) return "";
  return `<g opacity="0.5">
<circle cx="850" cy="1352" r="63" fill="none" stroke="#a54e4e" stroke-width="5"/>
<circle cx="850" cy="1352" r="48" fill="none" stroke="#a54e4e" stroke-width="2" stroke-dasharray="5 4"/>
<path d="M820 1352 h60 M850 1322 v60" stroke="#a54e4e" stroke-width="4"/>
</g>`;
}

export function buildPharmacyGuideReceiptSidecar(index, random, document) {
  const style = pharmacyGuideStyleForIndex(index);
  const { context } = document;
  const medication = medicationRegions(document, random, style);
  const receipt = receiptRegions(document, style);
  const lastRowBottom = style.rowStart + Math.max(0, document.medications.length - 1) * style.rowGap + 54;
  const cautionY = Math.max(760, lastRowBottom + 50);
  const storageY = cautionY + 55;
  const ruledStart = storageY + 105;
  const mainRight = medication.columns.mainRight;
  const regions = [
    region("guide-brand", context.pharmacy, 74, 70, 380, 46, { semanticRole: "pharmacy", fontSize: 32 }),
    region("guide-title", "복약안내", 690, 70, 220, 46, { semanticRole: "document_title", fontSize: 34 }),
    region("guide-patient", `복약자 ${context.patient}`, 70, 145, 285, 31, { semanticRole: "patient", fontSize: 22 }),
    region("guide-clinic", `처방 ${context.clinic}`, 365, 145, 315, 31, { semanticRole: "clinic", fontSize: 22 }),
    region("guide-date", `조제 ${context.dispense_date}`, 690, 145, 250, 31, { semanticRole: "date", regionClass: "distractor", fontSize: 21 }),
    region("guide-summary", `총 ${document.medications.length}종 · 정해진 용법대로 복용`, 70, 205, 410, 29, { semanticRole: "instruction", regionClass: "distractor", fontSize: 18 }),
    ...medication.regions,
    region("guide-caution", "주의사항: 이상반응이 있으면 복용을 중단하고 의사 또는 약사와 상의하세요.", 70, cautionY, 810, 31, { semanticRole: "instruction", regionClass: "distractor", fontSize: 18 }),
    region("guide-storage", "보관방법: 직사광선과 습기를 피하고 어린이의 손이 닿지 않는 곳에 보관하세요.", 70, storageY, 810, 31, { semanticRole: "storage", regionClass: "distractor", fontSize: 17 }),
    region("guide-legal-1", "※ 의약품은 처방된 용법과 용량을 지켜 복용하세요.", 70, 1450, 640, 23, { semanticRole: "instruction", regionClass: "distractor", fontSize: 13 }),
    region("guide-legal-2", "※ 문의사항은 조제한 약국 또는 의료기관에 확인하세요.", 70, 1480, 640, 23, { semanticRole: "instruction", regionClass: "distractor", fontSize: 13 }),
    ...(style.stamp ? [region("guide-stamp-label", "복약상담", 810, 1337, 92, 26, { semanticRole: "label", regionClass: "distractor", fontSize: 15 })] : []),
    ...receipt.regions,
  ];
  const ruledLines = Array.from({ length: 5 }, (_, row) => {
    const y = ruledStart + row * 67;
    if (y > 1410) return "";
    return `<line x1="70" y1="${y}" x2="${mainRight - 30}" y2="${y}" stroke="${style.border}" stroke-width="1" opacity="0.55"/>`;
  }).join("\n");
  const headerBandWidth = mainRight - 52;
  const seamX = style.receiptX - 5;
  const headerBand = `<rect class="guide-header-band" x="45" y="238" width="${headerBandWidth}" height="58" fill="${style.accent}" opacity="${style.id === "low_contrast_blue" ? 0.62 : 0.9}"/>`;
  const emptyHeaderBand = `<line class="guide-header-band-empty" x1="45" y1="267" x2="${45 + headerBandWidth}" y2="267" stroke="${style.border}" stroke-width="2" opacity="0.72"/>`;
  const decoration = `${mainPaperSvg(style, mainRight)}
<rect x="45" y="50" width="${headerBandWidth}" height="100" rx="4" fill="${style.accentSoft}" opacity="0.72"/>
${headerBand}
<line class="receipt-perforation" x1="${seamX}" y1="45" x2="${seamX}" y2="1525" stroke="${style.border}" stroke-width="1.4" stroke-dasharray="4 5"/>
${rowDecorations(index, document, style, medication.columns)}
${receiptDecorations(document, style, receipt)}
${ruledLines}
${qrDistractor(index, mainRight - 120, 1370, 6)}
${stampDecoration(style)}`;
  return {
    layout_family: document.layout_family,
    visual_style: style.id,
    scenario_tags: [
      "medication_bag",
      "pharmacy_guide",
      "receipt_sidecar",
      "multi_style_print",
      `print_style_${style.id}`,
      document.medications.length === 1 ? "single_medication" : "multi_medication",
      "large_blank_body",
    ],
    risk_tags: ["small_text", "row_association", "column_association", "distractor_text", "numeric_distractor", "receipt_sidecar", "pictogram", "print_style_variation"],
    regions,
    decorations: decoration,
    decoration_adaptations: {
      header_band: {
        full: headerBand,
        empty: emptyHeaderBand,
      },
    },
  };
}