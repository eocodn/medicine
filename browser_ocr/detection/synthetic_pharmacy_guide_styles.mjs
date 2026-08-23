import {
  fittedFontSize,
  region,
  scaledFont,
} from "./synthetic_layout_primitives.mjs";
export {
  PHARMACY_GUIDE_STYLE_IDS,
  pharmacyGuideStyleForIndex,
} from "./synthetic_pharmacy_guide_templates.mjs";
import { pharmacyGuideStyleForIndex } from "./synthetic_pharmacy_guide_templates.mjs";

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
  const grain = Array.from({ length: 15 }, (_, dot) => {
    const mixed = (Math.imul(index + 11, 2654435761) ^ Math.imul(row + 7, 2246822519) ^ Math.imul(dot + 3, 3266489917)) >>> 0;
    const gx = x + 5 + (mixed % 96);
    const gy = y + 5 + ((mixed >>> 8) % 54);
    const radius = 0.6 + ((mixed >>> 16) % 3) * 0.45;
    return `<circle cx="${gx}" cy="${gy}" r="${radius}" fill="${mixed & 1 ? "#8f8e89" : "#f5f3ed"}" opacity="0.18"/>`;
  }).join("\n");
  const body = shape === 0
    ? `<ellipse cx="${x + 52}" cy="${y + 31}" rx="28" ry="19" fill="#d8d6d0" stroke="#aaa7a0"/><path d="M${x + 29} ${y + 31} h46" stroke="#aaa8a1"/><ellipse cx="${x + 45}" cy="${y + 24}" rx="12" ry="7" fill="#fff" opacity="0.22"/>`
    : shape === 1
      ? `<rect x="${x + 22}" y="${y + 13}" width="62" height="36" rx="18" fill="#d9d8d3" stroke="#aaa7a0"/><path d="M${x + 53} ${y + 13} v36" stroke="#aaa8a1"/><ellipse cx="${x + 42}" cy="${y + 22}" rx="12" ry="6" fill="#fff" opacity="0.2"/>`
      : shape === 2
        ? `<ellipse cx="${x + 39}" cy="${y + 31}" rx="20" ry="16" fill="#d8d7d2" stroke="#aaa7a0"/><ellipse cx="${x + 71}" cy="${y + 31}" rx="20" ry="16" fill="#ece9df" stroke="#b5b0a5"/><circle cx="${x + 34}" cy="${y + 25}" r="5" fill="#fff" opacity="0.2"/>`
        : `<rect x="${x + 24}" y="${y + 16}" width="58" height="30" rx="15" fill="#d5d5d1" stroke="#a9a9a3" transform="rotate(-8 ${x + 53} ${y + 31})"/><path d="M${x + 38} ${y + 31} h31" stroke="#aaa8a1" transform="rotate(-8 ${x + 53} ${y + 31})"/>`;
  return `<g class="medication-thumbnail">
<rect x="${x}" y="${y}" width="106" height="64" rx="2" fill="#d8d7d2" stroke="${style.border}" opacity="0.92"/>
<g class="thumbnail-grain">${grain}</g>
<path d="M${x + 3} ${y + 10} H${x + 103} M${x + 3} ${y + 45} H${x + 103}" stroke="#fff" opacity="0.3"/>
<ellipse class="pill-shadow" cx="${x + 55}" cy="${y + 42}" rx="34" ry="11" fill="#4f4e4a" opacity="0.18"/>
${body}
<ellipse class="pill-highlight" cx="${x + 42}" cy="${y + 22}" rx="9" ry="5" fill="#fff" opacity="0.28"/>
</g>`;
}

function receiptRegions(document, style) {
  const x = style.receiptX + 14;
  const valueX = style.receiptX + 148;
  const rowStart = 198;
  const rowGap = 48;
  const regions = [
    region("receipt-title", "약제비 계산서·영수증", x, 68, 232, 31, { semanticRole: "receipt", regionClass: "distractor", fontSize: 19, textFill: style.receiptHeaderText || null }),
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

function lowerGuidePanel(style, y, mainRight) {
  const width = mainRight - 110;
  if (style.lowerPanel === "schedule" || style.lowerPanel === "compact_schedule") {
    const labels = ["아침", "점심", "저녁", "취침전"];
    const compact = style.lowerPanel === "compact_schedule";
    const regions = [
      region("guide-lower-title", "다음에 복용할 시간", 82, y + 28, 240, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 17 }),
      ...labels.map((label, index) => region(`guide-lower-slot-${index}`, label, 285 + index * 145, y + 28, 105, 26, { semanticRole: "label", regionClass: "distractor", fontSize: 16 })),
      region("guide-lower-note", compact ? "식후 30분 · 정해진 시간에 복용" : "복용 후 해당 시간에 표시해 주세요.", 82, y + 112, 420, 26, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
    ];
    const columns = Array.from({ length: 4 }, (_, index) => {
      const x = 260 + index * 145;
      return `<line x1="${x}" y1="${y + 10}" x2="${x}" y2="${y + 150}" stroke="${style.border}" stroke-width="1" opacity="0.75"/><rect x="${x + 38}" y="${y + 86}" width="18" height="18" fill="none" stroke="${style.border}"/>`;
    }).join("\n");
    return {
      regions,
      decorations: `<g class="guide-lower-${compact ? "compact-schedule" : "schedule"}-panel"><rect x="70" y="${y}" width="${width}" height="160" rx="13" fill="${style.accentSoft}" opacity="0.42" stroke="${style.border}"/><line x1="70" y1="${y + 62}" x2="${70 + width}" y2="${y + 62}" stroke="${style.border}"/>${columns}</g>`,
      bottom: y + 160,
    };
  }
  if (style.lowerPanel === "storage") {
    const items = ["직사광선을 피해 보관", "어린이 손이 닿지 않는 곳", "습기가 적은 곳", "실온 보관"];
    const regions = [
      region("guide-lower-title", "보관방법", 88, y + 30, 170, 28, { semanticRole: "storage", regionClass: "distractor", fontSize: 18 }),
      ...items.map((text, index) => region(`guide-lower-storage-${index}`, text, 92 + index * Math.floor(width / 4), y + 117, Math.floor(width / 4) - 10, 48, { semanticRole: "storage", regionClass: "distractor", fontSize: 14 })),
    ];
    const icons = items.map((_, index) => {
      const cx = 145 + index * Math.floor(width / 4);
      return `<circle cx="${cx}" cy="${y + 78}" r="24" fill="none" stroke="${style.accent}" stroke-width="3"/><path d="M${cx - 11} ${y + 78} h22 M${cx} ${y + 67} v22" stroke="${style.accent}" stroke-width="2" opacity="0.72"/>`;
    }).join("\n");
    return {
      regions,
      decorations: `<g class="guide-lower-storage-panel"><rect x="70" y="${y}" width="${width}" height="190" rx="9" fill="${style.accentSoft}" opacity="0.26" stroke="${style.border}"/>${icons}</g>`,
      bottom: y + 190,
    };
  }
  if (style.lowerPanel === "pediatric") {
    const regions = [
      region("guide-lower-title", "우리 아이 복약 체크", 92, y + 28, 240, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 17 }),
      region("guide-lower-pediatric-0", "정해진 용법·용량을 보호자가 확인해 주세요.", 128, y + 78, 620, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-pediatric-1", "복용 후 이상반응이 있으면 약사와 상의하세요.", 128, y + 124, 620, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-pediatric-2", "다른 약·식품과 함께 먹을 때는 먼저 확인하세요.", 128, y + 170, 620, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
    ];
    return {
      regions,
      decorations: `<g class="guide-lower-pediatric-panel"><rect x="70" y="${y}" width="${width}" height="220" rx="18" fill="${style.accentSoft}" opacity="0.34" stroke="${style.border}"/><rect x="70" y="${y}" width="${width}" height="48" rx="18" fill="${style.secondary}" opacity="0.48"/><circle cx="100" cy="${y + 91}" r="13" fill="${style.secondary}" opacity="0.55"/><circle cx="100" cy="${y + 137}" r="13" fill="${style.accent}" opacity="0.55"/><circle cx="100" cy="${y + 183}" r="13" fill="${style.secondary}" opacity="0.55"/></g>`,
      bottom: y + 220,
    };
  }
  if (style.lowerPanel === "monochrome") {
    const regions = [
      region("guide-lower-title", "주의 · 안내", 88, y + 27, 180, 27, { semanticRole: "instruction", regionClass: "distractor", fontSize: 17 }),
      region("guide-lower-mono-0", "정해진 용법·용량을 지켜 복용하세요.", 92, y + 78, 630, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-mono-1", "졸음·어지러움이 있으면 운전에 주의하세요.", 92, y + 122, 630, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-mono-2", "이상반응이 나타나면 복용을 중단하고 상담하세요.", 92, y + 166, 630, 28, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
    ];
    return {
      regions,
      decorations: `<g class="guide-lower-monochrome-panel"><rect x="70" y="${y}" width="${width}" height="205" fill="none" stroke="${style.border}" stroke-width="1.5"/><line x1="70" y1="${y + 50}" x2="${70 + width}" y2="${y + 50}" stroke="${style.border}"/><line x1="84" y1="${y + 106}" x2="${70 + width - 14}" y2="${y + 106}" stroke="${style.border}" opacity="0.35"/><line x1="84" y1="${y + 150}" x2="${70 + width - 14}" y2="${y + 150}" stroke="${style.border}" opacity="0.35"/></g>`,
      bottom: y + 205,
    };
  }
  if (style.lowerPanel === "triple_info") {
    const cellWidth = Math.floor(width / 3);
    const regions = [
      region("guide-lower-title", "복용방법 안내", 86, y + 28, 190, 27, { semanticRole: "instruction", regionClass: "distractor", fontSize: 17 }),
      region("guide-lower-info-0", "아침·점심·저녁 · 식후 30분", 88, y + 78, cellWidth - 25, 76, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-info-1", "일반 주의사항 · 이상반응 시 상담", 88 + cellWidth, y + 78, cellWidth - 25, 76, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 }),
      region("guide-lower-info-2", "보관방법 · 습기·직사광선 주의", 88 + cellWidth * 2, y + 78, cellWidth - 25, 76, { semanticRole: "storage", regionClass: "distractor", fontSize: 15 }),
    ];
    return {
      regions,
      decorations: `<g class="guide-lower-triple-info-panel"><rect x="70" y="${y}" width="${width}" height="180" rx="12" fill="${style.accentSoft}" opacity="0.28" stroke="${style.border}"/><line x1="${70 + cellWidth}" y1="${y + 18}" x2="${70 + cellWidth}" y2="${y + 162}" stroke="${style.border}"/><line x1="${70 + cellWidth * 2}" y1="${y + 18}" x2="${70 + cellWidth * 2}" y2="${y + 162}" stroke="${style.border}"/></g>`,
      bottom: y + 180,
    };
  }
  const warnings = ["운전 또는 기계조작 시 주의", "졸림이 올 수 있습니다", "임의로 용량을 늘리지 마세요"];
  const regions = [
    region("guide-lower-title", "복약 시 주의사항", 92, y + 26, 220, 27, { semanticRole: "instruction", regionClass: "distractor", fontSize: 17, textFill: style.headerText }),
    ...warnings.map((text, index) => region(`guide-lower-warning-${index}`, text, 180, y + 76 + index * 48, 560, 26, { semanticRole: "instruction", regionClass: "distractor", fontSize: 15 })),
  ];
  const icons = warnings.map((_, index) => `<circle cx="118" cy="${y + 86 + index * 48}" r="19" fill="none" stroke="${style.accent}" stroke-width="3" opacity="0.75"/><path d="M111 ${y + 86 + index * 48} h14" stroke="${style.accent}" stroke-width="3"/>`).join("\n");
  return {
    regions,
    decorations: `<g class="guide-lower-warning-panel"><rect x="70" y="${y}" width="${width}" height="220" rx="8" fill="${style.accentSoft}" opacity="0.38" stroke="${style.border}"/><rect x="70" y="${y}" width="${width}" height="48" rx="8" fill="${style.accent}" opacity="0.72"/>${icons}</g>`,
    bottom: y + 220,
  };
}

function mainPaperSvg(style, mainRight) {
  if (style.receiptMode === "detached") {
    return `<path class="paper-silhouette" d="M30 30 Q${Math.round(mainRight * 0.5)} 18 ${mainRight - 8} 28 Q${mainRight + 1} 790 ${mainRight - 10} 1555 Q${Math.round(mainRight * 0.5)} 1564 28 1554 Q19 790 30 30 Z" fill="${style.paper}" stroke="${style.border}" stroke-width="1.5"/>
<path class="receipt-silhouette" d="M${style.receiptX + 3} 42 Q${Math.round((style.receiptX + 1262) / 2)} 34 1258 43 Q1264 780 1257 1528 Q${Math.round((style.receiptX + 1262) / 2)} 1537 ${style.receiptX + 4} 1529 Q${style.receiptX - 2} 790 ${style.receiptX + 3} 42 Z" fill="${style.receipt}" stroke="${style.border}" stroke-width="1.2"/>`;
  }
  if (style.receiptMode === "stapled") {
    return `<path class="paper-silhouette" d="M30 30 Q640 18 1258 30 Q1266 790 1256 1555 Q640 1565 28 1554 Q18 790 30 30 Z" fill="${style.paper}" stroke="${style.border}" stroke-width="1.5"/>
<path class="receipt-silhouette stapled-receipt" d="M${style.receiptX + 3} 48 Q1115 34 1255 54 L1255 865 Q1125 878 ${style.receiptX + 6} 858 Z" fill="${style.receipt}" stroke="${style.border}" stroke-width="1.2"/>
<line class="receipt-staple" x1="${style.receiptX + 55}" y1="54" x2="${style.receiptX + 105}" y2="48" stroke="#505050" stroke-width="5" stroke-linecap="round"/>`;
  }
  return `<path class="paper-silhouette" d="M30 30 Q640 18 1258 30 Q1266 790 1256 1555 Q640 1565 28 1554 Q18 790 30 30 Z" fill="${style.paper}" stroke="${style.border}" stroke-width="1.5"/>
<rect x="${style.receiptX}" y="38" width="${1262 - style.receiptX}" height="1494" fill="${style.receipt}" stroke="${style.border}" stroke-width="0.8"/>`;
}

function templateOverlay(style, mainRight) {
  const width = mainRight - 52;
  if (style.id === "teal_modern_grid") {
    return `<g class="guide-template-teal-modern-grid"><rect x="27" y="30" width="16" height="1522" fill="${style.accent}" opacity="0.82"/><rect x="${style.receiptX + 8}" y="48" width="${1248 - style.receiptX}" height="48" rx="5" fill="${style.secondary}" opacity="0.92"/><rect x="${style.receiptX + 8}" y="1185" width="${1248 - style.receiptX}" height="118" rx="8" fill="${style.accent}" opacity="0.82"/></g>`;
  }
  if (style.id === "pediatric_pastel") {
    return `<g class="guide-template-pediatric-pastel"><rect x="30" y="30" width="${mainRight - 38}" height="18" fill="${style.accent}" opacity="0.55"/><rect x="48" y="185" width="${width - 15}" height="34" rx="12" fill="${style.secondary}" opacity="0.35"/><circle cx="${style.receiptX + 205}" cy="1240" r="18" fill="${style.secondary}" opacity="0.38"/><circle cx="${style.receiptX + 230}" cy="1272" r="13" fill="${style.secondary}" opacity="0.26"/><path d="M${style.receiptX + 205} 1258 q18 38 7 78" fill="none" stroke="${style.secondary}" opacity="0.3"/></g>`;
  }
  if (style.id === "monochrome_stapled") {
    return `<g class="guide-template-monochrome-stapled"><rect x="48" y="50" width="${width}" height="92" fill="none" stroke="${style.border}" stroke-width="1.4"/><path d="M45 1510 h${Math.max(40, width - 10)}" stroke="${style.border}" stroke-width="1.2"/><rect x="${style.receiptX + 18}" y="102" width="${1240 - style.receiptX}" height="34" fill="none" stroke="${style.border}"/></g>`;
  }
  if (style.id === "lavender_dense") {
    return `<g class="guide-template-lavender-dense"><rect x="45" y="50" width="${width}" height="86" fill="${style.accent}" opacity="0.54"/><rect x="45" y="1460" width="${width}" height="60" fill="${style.accent}" opacity="0.56"/><line x1="385" y1="245" x2="385" y2="760" stroke="${style.border}"/><line x1="655" y1="245" x2="655" y2="760" stroke="${style.border}"/></g>`;
  }
  if (style.id === "yellow_blue_split") {
    return `<g class="guide-template-yellow-blue-split"><rect x="45" y="50" width="${Math.round(width * 0.56)}" height="100" fill="${style.accentSoft}" opacity="0.74"/><rect x="${45 + Math.round(width * 0.56)}" y="50" width="${Math.round(width * 0.44)}" height="100" fill="${style.accent}" opacity="0.82"/><rect x="${style.receiptX + 8}" y="45" width="${1248 - style.receiptX}" height="54" fill="${style.secondary}" opacity="0.8"/></g>`;
  }
  return "";
}

function templateExtraRegions(style) {
  if (style.id !== "teal_modern_grid") return [];
  return [
    region("guide-template-promo-title", "내 건강정보, 한번에!", style.receiptX + 23, 1204, 265, 28, { semanticRole: "label", regionClass: "distractor", fontSize: 16, textFill: "#f8fbfb" }),
    region("guide-template-promo-note", "복약알림과 처방내역을 확인하세요.", style.receiptX + 23, 1244, 265, 42, { semanticRole: "instruction", regionClass: "distractor", fontSize: 13, textFill: "#f8fbfb" }),
  ];
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
  const mainRight = medication.columns.mainRight;
  const lower = lowerGuidePanel(style, Math.max(storageY + 90, 875), mainRight);
  const ruledStart = lower.bottom + 42;
  const regions = [
    region("guide-brand", context.pharmacy, 74, 70, 380, 46, { semanticRole: "pharmacy", fontSize: 32 }),
    region("guide-title", "복약안내", 690, 70, 220, 46, { semanticRole: "document_title", fontSize: 34, textFill: style.titleText || null }),
    region("guide-patient", `복약자 ${context.patient}`, 70, 145, 285, 31, { semanticRole: "patient", fontSize: 22 }),
    region("guide-clinic", `처방 ${context.clinic}`, 365, 145, 315, 31, { semanticRole: "clinic", fontSize: 22 }),
    region("guide-date", `조제 ${context.dispense_date}`, 690, 145, 250, 31, { semanticRole: "date", regionClass: "distractor", fontSize: 21 }),
    region("guide-summary", `총 ${document.medications.length}종 · 정해진 용법대로 복용`, 70, 205, 410, 29, { semanticRole: "instruction", regionClass: "distractor", fontSize: 18 }),
    ...medication.regions,
    region("guide-caution", "주의사항: 이상반응이 있으면 복용을 중단하고 의사 또는 약사와 상의하세요.", 70, cautionY, 810, 31, { semanticRole: "instruction", regionClass: "distractor", fontSize: 18 }),
    region("guide-storage", "보관방법: 직사광선과 습기를 피하고 어린이의 손이 닿지 않는 곳에 보관하세요.", 70, storageY, 810, 31, { semanticRole: "storage", regionClass: "distractor", fontSize: 17 }),
    ...lower.regions,
    region("guide-legal-1", "※ 의약품은 처방된 용법과 용량을 지켜 복용하세요.", 70, 1450, 640, 23, { semanticRole: "instruction", regionClass: "distractor", fontSize: 13 }),
    region("guide-legal-2", "※ 문의사항은 조제한 약국 또는 의료기관에 확인하세요.", 70, 1480, 640, 23, { semanticRole: "instruction", regionClass: "distractor", fontSize: 13 }),
    ...(style.stamp ? [region("guide-stamp-label", "복약상담", 810, 1337, 92, 26, { semanticRole: "label", regionClass: "distractor", fontSize: 15 })] : []),
    ...templateExtraRegions(style),
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
${templateOverlay(style, mainRight)}
${headerBand}
<line class="receipt-perforation" x1="${seamX}" y1="45" x2="${seamX}" y2="1525" stroke="${style.border}" stroke-width="1.4" stroke-dasharray="4 5"/>
${rowDecorations(index, document, style, medication.columns)}
${receiptDecorations(document, style, receipt)}
${lower.decorations}
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
      `receipt_mode_${style.receiptMode}`,
      `lower_panel_${style.lowerPanel}`,
      document.medications.length === 1 ? "single_medication" : "multi_medication",
      "structured_lower_panel",
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