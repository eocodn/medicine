import { CONTEXT_TEXT, LAYOUT_FAMILIES, PRODUCTS } from "./synthetic_catalog.mjs";

export const DOCUMENT_WIDTH = 1280;
export const DOCUMENT_HEIGHT = 1600;

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function quad(x, y, width, height) {
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

function region(regionId, text, x, y, width, height, {
  critical = false,
  associationGroup = "document",
  semanticRole = "context",
  regionClass = "context",
  fontSize = 38,
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
  };
}

function pick(values, random) {
  return values[Math.floor(random() * values.length)];
}

function regimenRegions(prefix, group, y, product, rowIndex, fontSize = 40) {
  return [
    region(`${prefix}-product`, product, 75, y, 290, 58, { critical: true, associationGroup: group, semanticRole: "product", regionClass: "medication", fontSize }),
    region(`${prefix}-dose`, rowIndex % 3 === 2 ? "0.5정" : `${1 + (rowIndex % 2)}정`, 430, y, 120, 58, { critical: true, associationGroup: group, semanticRole: "dose", regionClass: "medication", fontSize }),
    region(`${prefix}-freq`, `${2 + (rowIndex % 2)}회`, 660, y, 110, 58, { critical: true, associationGroup: group, semanticRole: "frequency", regionClass: "medication", fontSize }),
    region(`${prefix}-days`, `${3 + (rowIndex % 5)}일`, 875, y, 110, 58, { critical: true, associationGroup: group, semanticRole: "duration", regionClass: "medication", fontSize }),
    region(`${prefix}-meal`, rowIndex % 2 ? "식후 30분" : "아침 저녁", 1020, y, 180, 58, { associationGroup: group, semanticRole: "instruction", regionClass: "context", fontSize: fontSize - 4 }),
  ];
}

function prescriptionTable(index, random) {
  const regions = [
    region("title", "처 방 전", 490, 70, 300, 70, { semanticRole: "document_title", regionClass: "context", fontSize: 54 }),
    region("clinic", `의료기관 ${pick(CONTEXT_TEXT.clinics, random)}`, 70, 160, 360, 45, { semanticRole: "clinic", regionClass: "context", fontSize: 31 }),
    region("patient", `환자 ${pick(CONTEXT_TEXT.patients, random)}`, 485, 160, 260, 45, { semanticRole: "patient", regionClass: "context", fontSize: 31 }),
    region("date", `발행일 2026-08-${String(10 + index % 18).padStart(2, "0")}`, 820, 160, 350, 45, { semanticRole: "date", regionClass: "distractor", fontSize: 31 }),
    region("h-product", "약품명", 75, 285, 250, 48, { semanticRole: "header", fontSize: 34 }),
    region("h-dose", "1회 투약량", 430, 285, 210, 48, { semanticRole: "header", fontSize: 34 }),
    region("h-freq", "1일 횟수", 660, 285, 180, 48, { semanticRole: "header", fontSize: 34 }),
    region("h-days", "총 일수", 875, 285, 140, 48, { semanticRole: "header", fontSize: 34 }),
    region("h-note", "용법", 1040, 285, 130, 48, { semanticRole: "header", fontSize: 34 }),
  ];
  for (let row = 0; row < 5; row += 1) {
    regions.push(...regimenRegions(`r${row}`, `med-${row}`, 385 + row * 145, pick(PRODUCTS, random), row, 39));
  }
  regions.push(
    region("footer-warning", "※ 의약품 복용 전 약사 또는 의사의 설명을 확인하세요.", 80, 1190, 840, 40, { semanticRole: "instruction", regionClass: "distractor", fontSize: 27 }),
    region("footer-phone", "문의 02-000-0000", 940, 1190, 240, 40, { semanticRole: "phone", regionClass: "distractor", fontSize: 27 }),
  );
  const rowLines = Array.from({ length: 6 }, (_, row) => `<line x1="60" y1="${350 + row * 145}" x2="1210" y2="${350 + row * 145}" stroke="#d0d0d0"/>`).join("\n");
  return {
    layout_family: "prescription_table",
    scenario_tags: ["prescription", "table", "multi_medication"],
    risk_tags: ["row_association", "column_association", "small_text"],
    regions,
    decorations: `<rect x="40" y="35" width="1200" height="1505" fill="#ffffff" stroke="#c4c4c4" stroke-width="2"/>
<rect x="55" y="260" width="1165" height="870" fill="none" stroke="#b0b0b0" stroke-width="2"/>
${rowLines}`,
  };
}

function compactPrescriptionForm(index, random) {
  const regions = [
    region("title", "처 방 전", 500, 55, 280, 58, { semanticRole: "document_title", fontSize: 48 }),
    region("rx-number", `처방전 발급번호 RX-${String(17000 + index).padStart(6, "0")}`, 65, 135, 370, 34, { semanticRole: "receipt", regionClass: "distractor", fontSize: 24 }),
    region("use-period", "사용기간 발행일로부터 3일", 820, 135, 340, 34, { semanticRole: "date", regionClass: "distractor", fontSize: 24 }),
    region("patient", `환자성명 ${pick(CONTEXT_TEXT.patients, random)}`, 65, 205, 300, 36, { semanticRole: "patient", fontSize: 27 }),
    region("patient-id", "환자번호 00012345", 400, 205, 270, 36, { semanticRole: "receipt", regionClass: "distractor", fontSize: 25 }),
    region("clinic", `의료기관 ${pick(CONTEXT_TEXT.clinics, random)}`, 65, 258, 360, 36, { semanticRole: "clinic", fontSize: 27 }),
    region("prescriber", "처방의 홍길동  면허번호 12345", 500, 258, 450, 36, { semanticRole: "prescriber", fontSize: 25 }),
    region("clinic-phone", "TEL 02-234-5678", 980, 258, 210, 36, { semanticRole: "phone", regionClass: "distractor", fontSize: 24 }),
    region("head-product", "약품명", 65, 350, 300, 36, { semanticRole: "header", fontSize: 27 }),
    region("head-dose", "1회 투약량", 440, 350, 155, 36, { semanticRole: "header", fontSize: 25 }),
    region("head-freq", "1일 투여횟수", 635, 350, 165, 36, { semanticRole: "header", fontSize: 25 }),
    region("head-days", "총 투약일수", 840, 350, 145, 36, { semanticRole: "header", fontSize: 25 }),
    region("head-note", "용법", 1045, 350, 100, 36, { semanticRole: "header", fontSize: 25 }),
  ];
  for (let row = 0; row < 6; row += 1) {
    const group = `compact-rx-${row}`;
    const y = 420 + row * 108;
    regions.push(
      region(`cr${row}-product`, pick(PRODUCTS, random), 65, y, 330, 34, { critical: true, associationGroup: group, semanticRole: "product", regionClass: "medication", fontSize: 25 }),
      region(`cr${row}-dose`, row % 4 === 2 ? "0.5정" : "1정", 445, y, 105, 34, { critical: true, associationGroup: group, semanticRole: "dose", regionClass: "medication", fontSize: 24 }),
      region(`cr${row}-freq`, `${2 + row % 2}회`, 665, y, 90, 34, { critical: true, associationGroup: group, semanticRole: "frequency", regionClass: "medication", fontSize: 24 }),
      region(`cr${row}-days`, `${3 + row % 5}일`, 865, y, 90, 34, { critical: true, associationGroup: group, semanticRole: "duration", regionClass: "medication", fontSize: 24 }),
      region(`cr${row}-note`, row % 2 ? "식후30분" : "아침저녁", 1030, y, 145, 34, { associationGroup: group, semanticRole: "instruction", fontSize: 23 }),
    );
  }
  regions.push(
    region("injection-note", "주사제 처방 없음", 65, 1115, 250, 32, { semanticRole: "instruction", regionClass: "distractor", fontSize: 23 }),
    region("special-note", "특이사항 없음", 400, 1115, 230, 32, { semanticRole: "instruction", regionClass: "distractor", fontSize: 23 }),
    region("signature", "처방의 서명 (인)", 900, 1200, 250, 36, { semanticRole: "signature", regionClass: "distractor", fontSize: 25 }),
    region("pharmacy-area", "조제기관 기재란", 65, 1290, 240, 34, { semanticRole: "header", fontSize: 24 }),
    region("dispense-date", `조제일 2026.08.${String(10 + index % 18).padStart(2, "0")}`, 380, 1290, 300, 34, { semanticRole: "date", regionClass: "distractor", fontSize: 24 }),
    region("pharmacist", "조제약사 서명 (인)", 830, 1290, 300, 34, { semanticRole: "signature", regionClass: "distractor", fontSize: 24 }),
  );
  const rows = Array.from({ length: 7 }, (_, row) => `<line x1="50" y1="${400 + row * 108}" x2="1225" y2="${400 + row * 108}" stroke="#9fa4a8"/>`).join("\n");
  return {
    layout_family: "compact_prescription_form",
    scenario_tags: ["prescription", "administrative_dense", "table", "multi_medication"],
    risk_tags: ["small_text", "row_association", "column_association", "distractor_text"],
    regions,
    decorations: `<rect x="35" y="25" width="1210" height="1530" fill="#fff" stroke="#555" stroke-width="2"/>
<rect x="50" y="325" width="1175" height="825" fill="none" stroke="#7d8388" stroke-width="1.5"/>
${rows}
<rect x="50" y="1260" width="1175" height="180" fill="none" stroke="#92979b"/>`,
  };
}

function legacyPreprintedMedicationBag(index, random) {
  const blue = "#1f6ea9";
  const regions = [
    region("brand", "조 제 약", 470, 65, 330, 58, { semanticRole: "document_title", fontSize: 48 }),
    region("patient-label", "환자명", 75, 165, 105, 36, { semanticRole: "label", fontSize: 28 }),
    region("patient", pick(CONTEXT_TEXT.patients, random), 205, 165, 260, 36, { semanticRole: "patient", fontSize: 29 }),
    region("age-sex", "나이 ___  성별 □남 □여", 520, 165, 360, 36, { semanticRole: "patient", regionClass: "distractor", fontSize: 26 }),
    region("dispense-date", `조제일 2026.08.${String(10 + index % 18).padStart(2, "0")}`, 890, 165, 300, 36, { semanticRole: "date", regionClass: "distractor", fontSize: 25 }),
    region("daily", "1일", 90, 290, 90, 46, { associationGroup: "bag-regimen", semanticRole: "label", fontSize: 34 }),
    region("frequency", "3회", 205, 290, 100, 46, { critical: true, associationGroup: "bag-regimen", semanticRole: "frequency", regionClass: "medication", fontSize: 35 }),
    region("each", "1회", 355, 290, 90, 46, { associationGroup: "bag-regimen", semanticRole: "label", fontSize: 34 }),
    region("dose", "1포(정)", 475, 290, 150, 46, { critical: true, associationGroup: "bag-regimen", semanticRole: "dose", regionClass: "medication", fontSize: 35 }),
    region("days-label", "총", 680, 290, 60, 46, { associationGroup: "bag-regimen", semanticRole: "label", fontSize: 34 }),
    region("days", "5일분", 760, 290, 130, 46, { critical: true, associationGroup: "bag-regimen", semanticRole: "duration", regionClass: "medication", fontSize: 35 }),
    region("meal", "□ 식전  □ 식후 30분  □ 취침전", 90, 390, 650, 42, { associationGroup: "bag-regimen", semanticRole: "schedule", fontSize: 29 }),
    region("directions", "□ 아침   □ 점심   □ 저녁   □ 필요시", 90, 465, 690, 42, { associationGroup: "bag-regimen", semanticRole: "schedule", fontSize: 29 }),
    region("product-label", "약품명", 85, 600, 115, 34, { associationGroup: "bag-regimen", semanticRole: "header", fontSize: 27 }),
    region("product", pick(PRODUCTS, random), 225, 600, 390, 38, { critical: true, associationGroup: "bag-regimen", semanticRole: "product", regionClass: "medication", fontSize: 29 }),
    region("caution-title", "복약시 주의사항", 85, 735, 220, 36, { semanticRole: "header", fontSize: 28 }),
    region("caution-1", "정해진 용법과 용량을 지켜 복용하십시오.", 85, 800, 670, 34, { semanticRole: "instruction", regionClass: "distractor", fontSize: 24 }),
    region("caution-2", "이상반응이 있으면 약사 또는 의사와 상의하십시오.", 85, 855, 730, 34, { semanticRole: "instruction", regionClass: "distractor", fontSize: 24 }),
    region("pharmacy", pick(CONTEXT_TEXT.pharmacies, random), 80, 1250, 350, 46, { semanticRole: "pharmacy", fontSize: 36 }),
    region("phone", "TEL 02-1234-5678", 80, 1315, 300, 36, { semanticRole: "phone", regionClass: "distractor", fontSize: 25 }),
    region("address", "서울시 테스트구 약국로 00", 80, 1365, 390, 34, { semanticRole: "address", regionClass: "distractor", fontSize: 23 }),
  ];
  return {
    layout_family: "legacy_preprinted_medication_bag",
    scenario_tags: ["medication_bag", "legacy_preprinted", "checkbox_form"],
    risk_tags: ["small_text", "row_association", "shared_visual_style", "preprinted_lines"],
    regions,
    decorations: `<rect x="38" y="32" width="1204" height="1518" rx="12" fill="#fff" stroke="${blue}" stroke-width="5"/>
<rect x="62" y="145" width="1160" height="100" fill="none" stroke="${blue}" stroke-width="3"/>
<rect x="62" y="265" width="1160" height="270" fill="none" stroke="${blue}" stroke-width="4"/>
<line x1="62" y1="365" x2="1222" y2="365" stroke="${blue}" stroke-width="2"/>
<rect x="62" y="570" width="1160" height="360" fill="none" stroke="${blue}" stroke-width="3"/>
<path d="M70 1160 H1210 M70 1220 H1210" stroke="${blue}" stroke-width="2"/>
<circle cx="1100" cy="1300" r="72" fill="none" stroke="${blue}" stroke-width="5" opacity="0.55"/>
<text x="1040" y="1310" font-family="Noto Sans CJK KR, sans-serif" font-size="27" fill="${blue}" opacity="0.6">약사인</text>`,
  };
}

function classicMedicationBag(index, random) {
  const regions = [
    region("pharmacy", pick(CONTEXT_TEXT.pharmacies, random), 80, 80, 430, 75, { semanticRole: "pharmacy", regionClass: "context", fontSize: 52 }),
    region("patient", `환자명 ${pick(CONTEXT_TEXT.patients, random)}`, 80, 190, 330, 48, { semanticRole: "patient", regionClass: "context", fontSize: 34 }),
    region("dispense-date", `조제일 2026.08.${String(12 + index % 16).padStart(2, "0")}`, 470, 190, 330, 48, { semanticRole: "date", regionClass: "distractor", fontSize: 34 }),
    region("dose-guide", "복용방법", 80, 285, 200, 52, { semanticRole: "header", regionClass: "context", fontSize: 38 }),
  ];
  for (let block = 0; block < 3; block += 1) {
    const group = `bag-${block}`;
    const y = 400 + block * 260;
    regions.push(
      region(`b${block}-label`, "약명", 90, y, 100, 50, { associationGroup: group, semanticRole: "label", fontSize: 34 }),
      region(`b${block}-product`, pick(PRODUCTS, random), 230, y, 370, 55, { critical: true, associationGroup: group, semanticRole: "product", regionClass: "medication", fontSize: 40 }),
      region(`b${block}-dose`, `${1 + block % 2}정`, 230, y + 85, 100, 48, { critical: true, associationGroup: group, semanticRole: "dose", regionClass: "medication", fontSize: 34 }),
      region(`b${block}-freq`, `${2 + block % 2}회`, 390, y + 85, 100, 48, { critical: true, associationGroup: group, semanticRole: "frequency", regionClass: "medication", fontSize: 34 }),
      region(`b${block}-days`, `${5 + block}일`, 550, y + 85, 100, 48, { critical: true, associationGroup: group, semanticRole: "duration", regionClass: "medication", fontSize: 34 }),
      region(`b${block}-instruction`, block % 2 ? "아침·저녁 식후" : "매 식후 30분", 740, y + 85, 310, 48, { associationGroup: group, semanticRole: "instruction", regionClass: "context", fontSize: 31 }),
    );
  }
  regions.push(
    region("checkbox-label", "아침   점심   저녁   취침전", 110, 1210, 520, 42, { semanticRole: "schedule", regionClass: "distractor", fontSize: 30 }),
    region("pharmacy-phone", "약국전화 02-123-4567", 760, 1340, 350, 40, { semanticRole: "phone", regionClass: "distractor", fontSize: 28 }),
  );
  return {
    layout_family: "classic_medication_bag",
    scenario_tags: ["medication_bag", "classic_bag", "multi_medication"],
    risk_tags: ["row_association", "shared_visual_style"],
    regions,
    decorations: `<rect x="45" y="35" width="1190" height="1510" rx="34" fill="#fffdf5" stroke="#d4d0c4" stroke-width="3"/>
<path d="M45 270 H1235" stroke="#a8a8a0" stroke-width="2"/>
<rect x="90" y="1195" width="24" height="24" fill="none" stroke="#777"/><rect x="215" y="1195" width="24" height="24" fill="none" stroke="#777"/><rect x="340" y="1195" width="24" height="24" fill="none" stroke="#777"/><rect x="465" y="1195" width="24" height="24" fill="none" stroke="#777"/>`,
  };
}

function counselingMedicationBag(index, random) {
  const regions = [
    region("pharmacy", pick(CONTEXT_TEXT.pharmacies, random), 70, 70, 420, 64, { semanticRole: "pharmacy", fontSize: 46 }),
    region("guide-title", "복 약 안 내", 760, 75, 330, 60, { semanticRole: "document_title", fontSize: 44 }),
    region("patient", `복약자 ${pick(CONTEXT_TEXT.patients, random)}`, 80, 180, 300, 40, { semanticRole: "patient", fontSize: 29 }),
    region("clinic", `처방 ${pick(CONTEXT_TEXT.clinics, random)}`, 420, 180, 330, 40, { semanticRole: "clinic", fontSize: 29 }),
    region("date", `조제 2026-08-${String(10 + index % 18).padStart(2, "0")}`, 820, 180, 300, 40, { semanticRole: "date", regionClass: "distractor", fontSize: 29 }),
  ];
  for (let row = 0; row < 4; row += 1) {
    const group = `guide-${row}`;
    const y = 310 + row * 150;
    regions.push(...regimenRegions(`g${row}`, group, y, pick(PRODUCTS, random), row, 34));
  }
  regions.push(
    region("warning-title", "주의사항", 80, 970, 180, 44, { semanticRole: "header", regionClass: "context", fontSize: 34 }),
    region("warning-1", "운전 또는 위험한 기계 조작 전 졸림 여부를 확인하세요.", 80, 1040, 760, 38, { semanticRole: "instruction", regionClass: "distractor", fontSize: 26 }),
    region("warning-2", "이상반응이 지속되면 복용을 중단하지 말고 의료진과 상의하세요.", 80, 1100, 850, 38, { semanticRole: "instruction", regionClass: "distractor", fontSize: 26 }),
    region("storage", "보관방법  실온 / 직사광선 피함", 80, 1185, 500, 38, { semanticRole: "storage", regionClass: "distractor", fontSize: 26 }),
    region("receipt", "조제번호 000123   본인부담금 12,300원", 80, 1330, 610, 38, { semanticRole: "receipt", regionClass: "distractor", fontSize: 25 }),
    region("qr-label", "복약정보 QR", 925, 1325, 180, 36, { semanticRole: "qr_label", regionClass: "distractor", fontSize: 24 }),
  );
  return {
    layout_family: "counseling_medication_bag",
    scenario_tags: ["medication_bag", "counseling", "information_dense", "multi_medication"],
    risk_tags: ["small_text", "row_association", "distractor_text"],
    regions,
    decorations: `<rect x="42" y="35" width="1195" height="1515" rx="24" fill="#ffffff" stroke="#c7c7c7" stroke-width="2"/>
<rect x="60" y="270" width="1160" height="690" fill="#fbfbfb" stroke="#dedede"/>
<rect x="900" y="1265" width="210" height="210" fill="#f0f0f0" stroke="#555"/>
<path d="M920 1290 h45 v45 h-45z M995 1290 h70 v30 h-70z M920 1360 h30 v65 h-30z M980 1350 h80 v80 h-80z" fill="#333"/>`,
  };
}

function pharmacyInformationSheet(index, random) {
  const regions = [
    region("sheet-title", "조제약 복약정보", 430, 65, 420, 60, { semanticRole: "document_title", fontSize: 46 }),
    region("patient", `성명 ${pick(CONTEXT_TEXT.patients, random)}`, 70, 165, 260, 38, { semanticRole: "patient", fontSize: 28 }),
    region("clinic", `병원 ${pick(CONTEXT_TEXT.clinics, random)}`, 380, 165, 330, 38, { semanticRole: "clinic", fontSize: 28 }),
    region("date", `조제일 2026.08.${String(10 + index % 18).padStart(2, "0")}`, 790, 165, 330, 38, { semanticRole: "date", regionClass: "distractor", fontSize: 28 }),
    region("head-product", "약품명", 70, 260, 250, 38, { semanticRole: "header", fontSize: 27 }),
    region("head-dose", "1회량", 430, 260, 100, 38, { semanticRole: "header", fontSize: 27 }),
    region("head-freq", "횟수", 585, 260, 90, 38, { semanticRole: "header", fontSize: 27 }),
    region("head-days", "일수", 735, 260, 90, 38, { semanticRole: "header", fontSize: 27 }),
    region("head-note", "복약안내", 900, 260, 190, 38, { semanticRole: "header", fontSize: 27 }),
  ];
  for (let row = 0; row < 9; row += 1) {
    const group = `sheet-${row}`;
    const y = 335 + row * 82;
    const critical = row < 6;
    regions.push(
      region(`s${row}-product`, pick(PRODUCTS, random), 70, y, 300, 34, { critical, associationGroup: group, semanticRole: "product", regionClass: "medication", fontSize: 25 }),
      region(`s${row}-dose`, row % 4 === 1 ? "0.5정" : "1정", 430, y, 85, 34, { critical, associationGroup: group, semanticRole: "dose", regionClass: "medication", fontSize: 25 }),
      region(`s${row}-freq`, `${2 + row % 2}회`, 585, y, 80, 34, { critical, associationGroup: group, semanticRole: "frequency", regionClass: "medication", fontSize: 25 }),
      region(`s${row}-days`, `${3 + row % 5}일`, 735, y, 80, 34, { critical, associationGroup: group, semanticRole: "duration", regionClass: "medication", fontSize: 25 }),
      region(`s${row}-note`, pick(CONTEXT_TEXT.instructions, random), 900, y, 230, 34, { associationGroup: group, semanticRole: "instruction", regionClass: "context", fontSize: 23 }),
    );
  }
  regions.push(
    region("notice", "본 안내문은 복약 편의를 위한 정보이며 변경 사항은 약사에게 문의하세요.", 70, 1140, 900, 36, { semanticRole: "instruction", regionClass: "distractor", fontSize: 24 }),
    region("pharmacy", pick(CONTEXT_TEXT.pharmacies, random), 70, 1280, 280, 40, { semanticRole: "pharmacy", fontSize: 30 }),
    region("phone", "TEL 02-555-0101", 390, 1280, 250, 40, { semanticRole: "phone", regionClass: "distractor", fontSize: 28 }),
    region("receipt", "총 조제금액 31,600원", 760, 1280, 340, 40, { semanticRole: "receipt", regionClass: "distractor", fontSize: 28 }),
  );
  const lines = Array.from({ length: 10 }, (_, row) => `<line x1="55" y1="${315 + row * 82}" x2="1215" y2="${315 + row * 82}" stroke="#e2e2e2"/>`).join("\n");
  return {
    layout_family: "pharmacy_information_sheet",
    scenario_tags: ["information_sheet", "dense_small_print", "multi_medication"],
    risk_tags: ["small_text", "row_association", "column_association", "detector_resolution"],
    regions,
    decorations: `<rect x="40" y="30" width="1200" height="1520" fill="#fff" stroke="#ccc" stroke-width="2"/>
<rect x="55" y="235" width="1160" height="860" fill="none" stroke="#d4d4d4"/>
${lines}`,
  };
}

const BUILDERS = {
  prescription_table: prescriptionTable,
  compact_prescription_form: compactPrescriptionForm,
  legacy_preprinted_medication_bag: legacyPreprintedMedicationBag,
  classic_medication_bag: classicMedicationBag,
  counseling_medication_bag: counselingMedicationBag,
  pharmacy_information_sheet: pharmacyInformationSheet,
};

export function buildLayout(index, random) {
  const family = LAYOUT_FAMILIES[index % LAYOUT_FAMILIES.length];
  return BUILDERS[family](index, random);
}

export function renderLayoutRegions(regions, printer = { profile: "laser_clean" }) {
  const fontFamily = printer.profile === "ink_bleed"
    ? "Noto Serif CJK KR, serif"
    : "Noto Sans CJK KR, sans-serif";
  const fill = printer.profile === "low_toner" ? "#666" : "#202020";
  return regions.map((item) => {
    const [x, y] = item.text_origin || item.polygon[0];
    const baseline = y + Math.round(item.font_size_px * 0.82);
    const primary = `<text x="${x}" y="${baseline}" font-family="${fontFamily}" font-size="${item.font_size_px}" fill="${fill}">${escapeXml(item.text)}</text>`;
    if (printer.profile !== "ink_bleed") return primary;
    return `${primary}\n<text x="${x + 1.2}" y="${baseline + 0.7}" font-family="${fontFamily}" font-size="${item.font_size_px}" fill="#303030" opacity="0.24">${escapeXml(item.text)}</text>`;
  }).join("\n");
}