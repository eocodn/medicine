import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { validateCorpus } from "./contract.mjs";

const WIDTH = 1280;
const HEIGHT = 1600;
const PRODUCTS = ["타이레놀정", "이부프로펜정", "세트린정", "아목시실린캡슐", "레바미피드정", "메트포르민정"];

function rng(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

function escapeXml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function quad(x, y, width, height, angleDegrees = 0) {
  const angle = angleDegrees * Math.PI / 180;
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const horizontal = [width * cosine, width * sine];
  const vertical = [-height * sine, height * cosine];
  return [
    [x, y],
    [x + horizontal[0], y + horizontal[1]],
    [x + horizontal[0] + vertical[0], y + horizontal[1] + vertical[1]],
    [x + vertical[0], y + vertical[1]],
  ];
}

function polygonPoints(polygon) {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}

function renderRegion(region, fontSize) {
  const [x, y] = region.polygon[0];
  const [rightX, rightY] = region.polygon[1];
  const width = Math.hypot(rightX - x, rightY - y);
  const angle = Math.atan2(rightY - y, rightX - x) * 180 / Math.PI;
  const baseline = y + Math.round(fontSize * 0.82);
  return `<text x="${x}" y="${baseline}" textLength="${width}" lengthAdjust="spacingAndGlyphs" transform="rotate(${angle} ${x} ${y})" font-family="Noto Sans CJK KR, sans-serif" font-size="${fontSize}" fill="#202020">${escapeXml(region.text)}</text>`;
}

function region(regionId, text, x, y, width, height, critical, associationGroup, semanticRole, angleDegrees = 0) {
  return {
    region_id: regionId,
    text,
    polygon: quad(x, y, width, height, angleDegrees),
    critical,
    association_group: associationGroup,
    semantic_role: semanticRole,
  };
}

function prescriptionTable(index, random) {
  const angle = index % 2 ? 0.7 : 0;
  const rows = [];
  const regions = [
    region("h-product", "약품명", 70, 180, 250, 54, false, "header", "header", angle),
    region("h-dose", "1회 투약량", 420, 180, 220, 54, false, "header", "header", angle),
    region("h-freq", "1일 횟수", 720, 180, 200, 54, false, "header", "header", angle),
    region("h-days", "총 일수", 1010, 180, 150, 54, false, "header", "header", angle),
  ];
  for (let row = 0; row < 4; row += 1) {
    const group = `med-${row}`;
    const y = 320 + row * 180;
    const product = PRODUCTS[Math.floor(random() * PRODUCTS.length)];
    const dose = row === 2 ? "0.5정" : `${1 + (row % 2)}정`;
    const freq = `${2 + (row % 2)}회`;
    const days = `${3 + row * 2}일`;
    const fields = [
      region(`r${row}-product`, product, 70, y, 270, 62, true, group, "product", angle),
      region(`r${row}-dose`, dose, 430, y, 150, 62, true, group, "dose", angle),
      region(`r${row}-freq`, freq, 730, y, 140, 62, true, group, "frequency", angle),
      region(`r${row}-days`, days, 1010, y, 120, 62, true, group, "duration", angle),
    ];
    regions.push(...fields);
    rows.push(fields);
  }
  const lines = rows.map((_, row) => `<line x1="55" y1="${405 + row * 180}" x2="1190" y2="${405 + row * 180}" stroke="#d8d8d8"/>`).join("\n");
  return {
    scenario_tags: ["prescription_table", "multi_medication", ...(angle ? ["skewed_geometry"] : [])],
    risk_tags: ["small_text", "row_association", "column_association"],
    regions,
    decorations: `<rect x="45" y="130" width="1170" height="930" fill="none" stroke="#b8b8b8" stroke-width="2"/>\n${lines}`,
    fontSize: 42,
  };
}

function medicationBag(index, random) {
  const regions = [];
  for (let block = 0; block < 3; block += 1) {
    const group = `bag-${block}`;
    const y = 240 + block * 350;
    const product = PRODUCTS[Math.floor(random() * PRODUCTS.length)];
    regions.push(
      region(`b${block}-label`, "약명", 85, y, 110, 58, false, group, "label"),
      region(`b${block}-product`, product, 240, y, 360, 58, true, group, "product"),
      region(`b${block}-regimen-label`, "복용법", 85, y + 105, 140, 54, false, group, "label"),
      region(`b${block}-dose`, `${1 + (block % 2)}정`, 275, y + 105, 110, 54, true, group, "dose"),
      region(`b${block}-freq`, `${2 + (block % 2)}회`, 450, y + 105, 110, 54, true, group, "frequency"),
      region(`b${block}-days`, `${5 + block}일`, 625, y + 105, 110, 54, true, group, "duration"),
      region(`b${block}-meal`, "식후 30분", 805, y + 105, 220, 54, false, group, "instruction"),
    );
  }
  return {
    scenario_tags: ["medication_bag", "multi_medication", ...(index % 2 ? ["dense_blocks"] : [])],
    risk_tags: ["row_association", "shared_visual_style"],
    regions,
    decorations: `<rect x="45" y="100" width="1170" height="1280" rx="28" fill="#fffef8" stroke="#d0d0c8" stroke-width="3"/>`,
    fontSize: 44,
  };
}

function denseSmallPrint(index) {
  const regions = [];
  for (let row = 0; row < 12; row += 1) {
    const group = `dense-${row}`;
    const y = 180 + row * 90;
    const product = PRODUCTS[(index + row) % PRODUCTS.length];
    regions.push(
      region(`d${row}-product`, product, 70, y, 300, 38, row < 8, group, "product"),
      region(`d${row}-dose`, row % 3 === 0 ? "0.5정" : "1정", 430, y, 90, 38, row < 8, group, "dose"),
      region(`d${row}-freq`, `${2 + (row % 2)}회`, 585, y, 85, 38, row < 8, group, "frequency"),
      region(`d${row}-days`, `${3 + (row % 5)}일`, 735, y, 85, 38, row < 8, group, "duration"),
      region(`d${row}-note`, row % 2 ? "식후 30분" : "아침 저녁", 900, y, 190, 38, false, group, "instruction"),
    );
  }
  return {
    scenario_tags: ["dense_small_print", "multi_medication"],
    risk_tags: ["small_text", "row_association", "detector_resolution"],
    regions,
    decorations: `<rect x="45" y="90" width="1170" height="1220" fill="white" stroke="#d5d5d5"/>`,
    fontSize: 29,
  };
}

function renderSvg(sample) {
  const regions = sample.regions.map((item) => renderRegion(item, sample.fontSize)).join("\n");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
<rect width="${WIDTH}" height="${HEIGHT}" fill="#f7f7f5"/>
${sample.decorations}
${regions}
</svg>\n`;
}

function digest(content) {
  return createHash("sha256").update(content).digest("hex");
}

export async function generateSyntheticCorpus({ outputDir, count = 6, seed = 153 }) {
  if (!Number.isInteger(count) || count <= 0) throw new Error("count must be a positive integer");
  if (!Number.isInteger(seed)) throw new Error("seed must be an integer");
  await mkdir(join(outputDir, "images"), { recursive: true });
  const random = rng(seed);
  const samples = [];
  const builders = [prescriptionTable, medicationBag, denseSmallPrint];
  for (let index = 0; index < count; index += 1) {
    const built = builders[index % builders.length](index, random);
    const id = `synthetic-${String(index + 1).padStart(4, "0")}`;
    const image = `images/${id}.svg`;
    const svg = renderSvg(built);
    await writeFile(join(outputDir, image), svg);
    samples.push({
      id,
      image,
      image_sha256: digest(svg),
      width: WIDTH,
      height: HEIGHT,
      scenario_tags: built.scenario_tags,
      risk_tags: built.risk_tags,
      regions: built.regions,
    });
  }
  const corpus = validateCorpus({
    schema_version: 1,
    corpus_id: `synthetic-prescription-detection-v1-seed-${seed}-n-${count}`,
    synthetic_only: true,
    gates: {
      min_recall: 0.95,
      min_precision: 0.9,
      min_critical_box_recall: 0.98,
      max_merge_errors: 0,
      max_cross_association_merges: 0,
      max_split_errors: 0,
    },
    samples,
  });
  await writeFile(join(outputDir, "manifest.json"), `${JSON.stringify(corpus, null, 2)}\n`);
  return corpus;
}

export const syntheticDimensions = { width: WIDTH, height: HEIGHT };