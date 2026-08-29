import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const [, , command, sourceArg, targetArg, mode] = process.argv;
if (command !== "prepare" || !sourceArg || !targetArg || (mode !== "enabled" && mode !== "disabled")) {
  throw new Error("usage: build-capability.ts prepare <source-dir> <target-dir> <enabled|disabled>");
}

const source = resolve(sourceArg);
const target = resolve(targetArg);
const enabled = mode === "enabled";
const start = /^\s*\/\/ MEDICINE_OCR_START\s*$/;
const alternate = /^\s*\/\/ MEDICINE_OCR_ELSE\s*$/;
const end = /^\s*\/\/ MEDICINE_OCR_END\s*$/;

function filterCapability(text: string): string {
  const output: string[] = [];
  let branch: "common" | "ocr" | "no-ocr" = "common";
  for (const line of text.split(/(?<=\n)/)) {
    const marker = line.replace(/\r?\n$/, "");
    if (start.test(marker)) { branch = "ocr"; continue; }
    if (alternate.test(marker)) { branch = "no-ocr"; continue; }
    if (end.test(marker)) { branch = "common"; continue; }
    if (branch === "common" || (branch === "ocr" && enabled) || (branch === "no-ocr" && !enabled)) output.push(line);
  }
  if (branch !== "common") throw new Error("unterminated MEDICINE_OCR block");
  return output.join("");
}

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const entry of readdirSync(source, { withFileTypes: true })) {
  if (!entry.isFile() || !entry.name.endsWith(".ts")) continue;
  if (!enabled && entry.name === "ocr-intake.ts") continue;
  writeFileSync(join(target, entry.name), filterCapability(readFileSync(join(source, entry.name), "utf8")));
}

const baseConfig = JSON.parse(readFileSync(join(dirname(source), "tsconfig.json"), "utf8"));
baseConfig.compilerOptions = { ...(baseConfig.compilerOptions || {}), rootDir: ".", outDir: resolve(targetArg, "../dist") };
baseConfig.include = ["**/*.ts"];
delete baseConfig.exclude;
writeFileSync(join(target, "tsconfig.json"), JSON.stringify(baseConfig, null, 2) + "\n");
