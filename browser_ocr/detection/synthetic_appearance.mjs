import {
  BACKGROUND_PROFILES,
  MATERIAL_PROFILES,
  PRINTER_PROFILES,
  SCENE_PROP_PROFILES,
} from "./synthetic_catalog.mjs";

export function appearanceForIndex(index) {
  const material_profile = MATERIAL_PROFILES[index % MATERIAL_PROFILES.length];
  const printer_profile = PRINTER_PROFILES[Math.floor(index / MATERIAL_PROFILES.length) % PRINTER_PROFILES.length];
  // Scene texture is intentionally cycled independently from the print/material
  // strata. A small corpus must see every real-photo background family instead
  // of waiting for the full Cartesian product to roll over.
  const background_profile = BACKGROUND_PROFILES[Math.floor(index / 2) % BACKGROUND_PROFILES.length];
  const scene_prop_profile = SCENE_PROP_PROFILES[Math.floor(index / 3) % SCENE_PROP_PROFILES.length];
  const texture_seed = Math.imul(index + 1, 2654435761) >>> 0;
  const risk_tags = [];
  if (material_profile === "paper_folded") risk_tags.push("material_fold");
  if (material_profile === "paper_wrinkled") risk_tags.push("material_wrinkle");
  if (material_profile === "plastic_wrinkled") risk_tags.push("plastic_reflection");
  if (printer_profile !== "laser_clean") risk_tags.push("printer_degradation");
  if (scene_prop_profile !== "none") risk_tags.push("scene_layering");
  return { material_profile, printer_profile, background_profile, scene_prop_profile, texture_seed, risk_tags };
}

export function printerDescriptor(profile) {
  return { profile };
}

function seededOffset(seed, salt, magnitude) {
  const mixed = (Math.imul((seed ^ salt) >>> 0, 2246822519) ^ ((seed + salt) >>> 13)) >>> 0;
  return Math.round(((mixed / 0xffffffff) * 2 - 1) * magnitude);
}

export function renderMaterialOverlay(profile, width, height, seed = 0) {
  if (profile === "paper_plain") return "";
  if (profile === "paper_folded") {
    const firstY = Math.round(height * 0.36) + seededOffset(seed, 11, 28);
    const secondY = Math.round(height * 0.64) + seededOffset(seed, 29, 35);
    const verticalX = Math.round(width * 0.55) + seededOffset(seed, 47, 55);
    return `<defs>
<linearGradient id="fold-h-${seed}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="0.42" stop-color="#3b3934" stop-opacity="0.13"/><stop offset="0.54" stop-color="#fff" stop-opacity="0.2"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<linearGradient id="fold-v-${seed}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="0.46" stop-color="#47443f" stop-opacity="0.08"/><stop offset="0.56" stop-color="#fff" stop-opacity="0.12"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
</defs><g pointer-events="none">
<rect x="26" y="${firstY - 30}" width="${width - 52}" height="65" fill="url(#fold-h-${seed})" opacity="0.72"/>
<rect x="26" y="${secondY - 32}" width="${width - 52}" height="70" fill="url(#fold-h-${seed})" opacity="0.64"/>
<rect x="${verticalX - 24}" y="26" width="58" height="${height - 52}" fill="url(#fold-v-${seed})" opacity="0.52"/>
<path d="M35 ${firstY - 7} Q${Math.round(width * 0.48)} ${firstY + 12} ${width - 35} ${firstY - 3}" stroke="#4c4a45" stroke-width="18" opacity="0.055" fill="none"/>
<path d="M35 ${firstY + 3} Q${Math.round(width * 0.5)} ${firstY + 19} ${width - 35} ${firstY + 8}" stroke="#fff" stroke-width="9" opacity="0.18" fill="none"/>
<path d="M38 ${secondY - 8} Q${Math.round(width * 0.51)} ${secondY + 15} ${width - 38} ${secondY - 5}" stroke="#4c4a45" stroke-width="20" opacity="0.06" fill="none"/>
<path d="M38 ${secondY + 4} Q${Math.round(width * 0.48)} ${secondY + 20} ${width - 38} ${secondY + 8}" stroke="#fff" stroke-width="9" opacity="0.16" fill="none"/>
<path d="M${verticalX - 5} 40 Q${verticalX + 28} ${Math.round(height * 0.47)} ${verticalX - 12} ${height - 45}" stroke="#555" stroke-width="10" opacity="0.04" fill="none"/>
<path d="M${verticalX + 4} 40 Q${verticalX + 36} ${Math.round(height * 0.47)} ${verticalX - 3} ${height - 45}" stroke="#fff" stroke-width="5" opacity="0.12" fill="none"/>
</g>`;
  }
  if (profile === "paper_wrinkled") {
    const paths = Array.from({ length: 9 }, (_, index) => {
      const y = 120 + index * Math.round((height - 240) / 8) + seededOffset(seed, 101 + index * 13, 38);
      const bendA = seededOffset(seed, 211 + index * 17, 72);
      const bendB = seededOffset(seed, 307 + index * 19, 65);
      const startX = 45 + Math.max(0, seededOffset(seed, 401 + index, 55));
      const endX = width - 45 + Math.min(0, seededOffset(seed, 503 + index, 55));
      return `<path d="M${startX} ${y} C${Math.round(width * 0.28)} ${y + bendA} ${Math.round(width * 0.67)} ${y + bendB} ${endX} ${y + seededOffset(seed, 607 + index, 25)}" stroke="#4c4b48" stroke-width="7" opacity="0.035" fill="none"/>
<path d="M${startX} ${y + 5} C${Math.round(width * 0.28)} ${y + bendA + 5} ${Math.round(width * 0.67)} ${y + bendB + 5} ${endX} ${y + seededOffset(seed, 607 + index, 25) + 5}" stroke="#fff" stroke-width="5" opacity="0.13" fill="none"/>`;
    }).join("\n");
    return `<g pointer-events="none">${paths}</g>`;
  }
  return `<g pointer-events="none">
<path d="M65 210 Q${Math.round(width * 0.35)} 155 ${width - 70} 265" stroke="#fff" stroke-width="18" opacity="0.2" fill="none"/>
<path d="M90 520 Q${Math.round(width * 0.55)} 430 ${width - 80} 590" stroke="#5c5c5c" stroke-width="10" opacity="0.045" fill="none"/>
<path d="M120 910 Q${Math.round(width * 0.48)} 1040 ${width - 100} 920" stroke="#fff" stroke-width="22" opacity="0.17" fill="none"/>
<path d="M170 80 Q${Math.round(width * 0.28)} ${Math.round(height * 0.46)} ${Math.round(width * 0.37)} ${height - 100}" stroke="#fff" stroke-width="12" opacity="0.13" fill="none"/>
<path d="M${width - 170} 70 Q${Math.round(width * 0.76)} ${Math.round(height * 0.52)} ${Math.round(width * 0.66)} ${height - 90}" stroke="#666" stroke-width="7" opacity="0.04" fill="none"/>
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
  if (profile === "wood_warm") return "#8f603e";
  if (profile === "stone_speckle") return "#8f8d88";
  return "#cbc7bd";
}