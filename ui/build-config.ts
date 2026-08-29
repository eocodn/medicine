const { existsSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");

const outputDirectory = process.argv[2];
const mode = process.argv[3];
if (!outputDirectory || (mode !== "enabled" && mode !== "disabled")) {
  throw new Error("usage: build-config.ts <output-directory> <enabled|disabled>");
}

const indexPath = join(outputDirectory, "index.html");
const start = "<!-- MEDICINE_OCR_START -->";
const end = "<!-- MEDICINE_OCR_END -->";
let html = readFileSync(indexPath, "utf8");

if (mode === "disabled") {
  const block = new RegExp(`^[ \\t]*${start}[\\s\\S]*?^[ \\t]*${end}[ \\t]*\\r?\\n?`, "gm");
  html = html.replace(block, "");
  rmSync(join(outputDirectory, "ocr-intake.js"), { force: true });
} else {
  const markerLine = new RegExp(`^[ \\t]*(?:${start}|${end})[ \\t]*\\r?\\n`, "gm");
  html = html.replace(markerLine, "");
}

writeFileSync(indexPath, html);

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function configureMarkedFile(path, start, end) {
  if (!existsSync(path)) return;
  let text = readFileSync(path, "utf8");
  const escapedStart = escapeRegExp(start);
  const escapedEnd = escapeRegExp(end);
  if (mode === "disabled") {
    const block = new RegExp(`^[ \\t]*${escapedStart}[\\s\\S]*?^[ \\t]*${escapedEnd}[ \\t]*\\r?\\n?`, "gm");
    text = text.replace(block, "");
  } else {
    const markerLine = new RegExp(`^[ \\t]*(?:${escapedStart}|${escapedEnd})[ \\t]*\\r?\\n`, "gm");
    text = text.replace(markerLine, "");
  }
  writeFileSync(path, text);
}
configureMarkedFile(join(outputDirectory, "styles.css"), "/* MEDICINE_OCR_START */", "/* MEDICINE_OCR_END */");
