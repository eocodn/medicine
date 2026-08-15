import {
  BACKGROUND_PROFILES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
} from "./synthetic_catalog.mjs";

export function appearanceForIndex(index) {
  const material_profile = MATERIAL_PROFILES[index % MATERIAL_PROFILES.length];
  const printer_profile = PRINTER_PROFILES[Math.floor(index / MATERIAL_PROFILES.length) % PRINTER_PROFILES.length];
  const background_profile = BACKGROUND_PROFILES[
    Math.floor(index / (MATERIAL_PROFILES.length * PRINTER_PROFILES.length)) % BACKGROUND_PROFILES.length
  ];
  const risk_tags = [];
  if (material_profile === "paper_folded") risk_tags.push("material_fold");
  if (material_profile === "plastic_wrinkled") risk_tags.push("plastic_reflection");
  if (printer_profile !== "laser_clean") risk_tags.push("printer_degradation");
  return { material_profile, printer_profile, background_profile, risk_tags };
}

export function printerDescriptor(profile) {
  return { profile };
}

export function renderMaterialOverlay(profile, width, height) {
  if (profile === "paper_plain") return "";
  if (profile === "paper_folded") {
    return `<g pointer-events="none">
<path d="M${Math.round(width * 0.18)} 80 Q${Math.round(width * 0.42)} ${Math.round(height * 0.46)} ${Math.round(width * 0.72)} ${height - 90}" stroke="#777" stroke-width="9" opacity="0.06" fill="none"/>
<path d="M${Math.round(width * 0.18) + 8} 80 Q${Math.round(width * 0.42) + 8} ${Math.round(height * 0.46)} ${Math.round(width * 0.72) + 8} ${height - 90}" stroke="#fff" stroke-width="5" opacity="0.22" fill="none"/>
<path d="M85 ${Math.round(height * 0.62)} Q${Math.round(width * 0.52)} ${Math.round(height * 0.57)} ${width - 70} ${Math.round(height * 0.66)}" stroke="#555" stroke-width="7" opacity="0.045" fill="none"/>
</g>`;
  }
  return `<g pointer-events="none">
<g opacity="0.07">
  <ellipse cx="250" cy="1180" rx="78" ry="34" fill="#d9a4a4" transform="rotate(-18 250 1180)"/>
  <rect x="520" y="1115" width="145" height="58" rx="29" fill="#9eb7d8" transform="rotate(11 592 1144)"/>
  <circle cx="930" cy="1230" r="44" fill="#d8c894"/>
  <rect x="1010" y="1060" width="120" height="52" rx="26" fill="#b4d2a2" transform="rotate(-23 1070 1086)"/>
</g>
<path d="M65 210 Q${Math.round(width * 0.35)} 155 ${width - 70} 265" stroke="#fff" stroke-width="14" opacity="0.17" fill="none"/>
<path d="M90 520 Q${Math.round(width * 0.55)} 430 ${width - 80} 590" stroke="#777" stroke-width="8" opacity="0.05" fill="none"/>
<path d="M120 910 Q${Math.round(width * 0.48)} 1040 ${width - 100} 920" stroke="#fff" stroke-width="18" opacity="0.14" fill="none"/>
<path d="M170 80 Q${Math.round(width * 0.28)} ${Math.round(height * 0.46)} ${Math.round(width * 0.37)} ${height - 100}" stroke="#fff" stroke-width="8" opacity="0.1" fill="none"/>
</g>`;
}

export function renderPrinterOverlay(profile, width, height) {
  if (profile !== "low_toner") return "";
  const lines = [];
  for (let y = 115; y < height - 80; y += 145) {
    lines.push(`<line x1="55" y1="${y}" x2="${width - 55}" y2="${y}" stroke="#fff" stroke-width="7" opacity="0.11"/>`);
  }
  return `<g pointer-events="none">${lines.join("\n")}</g>`;
}

export function backgroundColor(profile) {
  if (profile === "desk_dark") return "#57534e";
  if (profile === "pharmacy_counter") return "#c8d7d2";
  return "#cbc7bd";
}