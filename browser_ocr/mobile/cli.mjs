#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const command = args[0];
const jsonOutput = args.includes("--json");

function option(name) {
  const index = args.lastIndexOf(name);
  if (index < 0) return null;
  const value = args[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`${name} requires a value`);
  return value;
}

function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function emit(value) {
  process.stdout.write(`${JSON.stringify(value, null, jsonOutput ? 0 : 2)}\n`);
}

async function inspectSource() {
  const manifest = JSON.parse(await readFile(join(here, "model-manifest.json"), "utf8"));
  const recognizerPath = join(here, "korean-recognition.onnx");
  const dictionaryPath = join(here, "korean-recognition-dictionary.json");
  const recognizerBytes = await readFile(recognizerPath);
  const dictionaryBytes = await readFile(dictionaryPath);
  const recognizerInfo = await stat(recognizerPath);
  return {
    status: "ok",
    scope: "source",
    model_id: manifest.model_id,
    selected_checkpoint_sha256: manifest.selected_recognizer.checkpoint_sha256,
    recognition_precision: manifest.runtime.recognition_precision,
    recognizer_size_bytes: recognizerInfo.size,
    recognizer_sha256_valid: sha256(recognizerBytes) === manifest.selected_recognizer.onnx_sha256
      && recognizerInfo.size === manifest.selected_recognizer.onnx_size_bytes,
    dictionary_sha256_valid: sha256(dictionaryBytes) === manifest.selected_recognizer.dictionary_sha256,
    parser: { enabled: false },
    fixed_eval_critical_exact: manifest.selected_recognizer.fixed_eval_critical_exact,
    fixed_eval_product_unseen_exact: manifest.selected_recognizer.fixed_eval_product_unseen_exact,
  };
}

async function inspectRuntime(root) {
  const runtimeRoot = resolve(root);
  const manifest = JSON.parse(await readFile(join(runtimeRoot, "runtime-manifest.json"), "utf8"));
  const files = {};
  let valid = true;
  for (const [name, expected] of Object.entries(manifest.files || {})) {
    const bytes = await readFile(join(runtimeRoot, name));
    const info = await stat(join(runtimeRoot, name));
    const match = sha256(bytes) === expected.sha256 && info.size === expected.size_bytes;
    files[name] = { sha256_valid: match, size_bytes: info.size };
    valid &&= match;
  }
  return {
    status: valid ? "ok" : "error",
    scope: "runtime",
    model_id: manifest.model_id,
    selected_checkpoint_sha256: manifest.selected_checkpoint_sha256,
    parser: manifest.parser ?? { enabled: false },
    runtime_files_valid: valid,
    file_count: Object.keys(files).length,
    files,
  };
}

try {
  if (command !== "inspect") throw new Error("usage: mobile/cli.mjs inspect [--runtime-dir DIR] [--json]");
  const runtimeDirectory = option("--runtime-dir");
  const result = runtimeDirectory ? await inspectRuntime(runtimeDirectory) : await inspectSource();
  emit(result);
  process.exitCode = result.status === "ok" ? 0 : 2;
} catch (error) {
  const result = { status: "error", error: error instanceof Error ? error.message : String(error) };
  if (jsonOutput) emit(result);
  else process.stderr.write(`${result.error}\n`);
  process.exitCode = 2;
}
