import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import yaml from "js-yaml";
import { runtimeFiles, runtimePayloadFiles } from "./runtime-layout.mjs";

const run = promisify(execFile);
const [downloadDirectory, outputDirectory] = process.argv.slice(2);
if (!downloadDirectory || !outputDirectory) {
  throw new Error("usage: node export_runtime.mjs DOWNLOAD_DIR OUTPUT_DIR");
}

const here = dirname(fileURLToPath(import.meta.url));
const modelManifestPath = join(here, "model-manifest.json");
const modelManifestBytes = await readFile(modelManifestPath);
const modelManifest = JSON.parse(modelManifestBytes.toString("utf8"));
const payloadFiles = runtimePayloadFiles();
const finalFiles = runtimeFiles();
const detectionArchive = modelManifest.sources?.detection?.archive;
const recognitionArchive = modelManifest.sources?.recognition?.archive;
if (!detectionArchive || !recognitionArchive) {
  throw new Error("model-manifest.json is missing detection/recognition archives");
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function listFiles(root, current = root) {
  const result = [];
  for (const entry of await readdir(current, { withFileTypes: true })) {
    const absolute = join(current, entry.name);
    if (entry.isDirectory()) result.push(...await listFiles(root, absolute));
    else if (entry.isFile()) result.push(relative(root, absolute).replaceAll("\\", "/"));
  }
  return result.sort();
}

function assertLayout(actual, expected, phase) {
  const expectedSorted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expectedSorted)) {
    throw new Error(`${phase} runtime layout mismatch: ${JSON.stringify(actual)}`);
  }
}

const unpackRoot = await mkdtemp(join(tmpdir(), "medicine-ocr-model-"));
const detectionDirectory = join(unpackRoot, "detection");
const recognitionDirectory = join(unpackRoot, "recognition");
await Promise.all([mkdir(detectionDirectory), mkdir(recognitionDirectory)]);
try {
  await Promise.all([
    run("tar", ["-xf", join(downloadDirectory, detectionArchive), "-C", detectionDirectory, "--strip-components=1"]),
    run("tar", ["-xf", join(downloadDirectory, recognitionArchive), "-C", recognitionDirectory, "--strip-components=1"]),
  ]);

  const recognitionConfig = yaml.load(await readFile(join(recognitionDirectory, "inference.yml"), "utf8"));
  const dictionary = recognitionConfig?.PostProcess?.character_dict;
  if (!Array.isArray(dictionary) || dictionary.length === 0
      || dictionary.some((value) => typeof value !== "string")) {
    throw new Error("recognition character dictionary is missing or malformed");
  }

  await rm(outputDirectory, { recursive: true, force: true });
  for (const directory of ["direct", "models", "ort", "licenses"]) {
    await mkdir(join(outputDirectory, directory), { recursive: true });
  }

  await Promise.all([
    copyFile(join(detectionDirectory, "inference.onnx"), join(outputDirectory, "models/detection.onnx")),
    copyFile(join(recognitionDirectory, "inference.onnx"), join(outputDirectory, "models/korean-recognition.onnx")),
    copyFile(join(here, "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs"), join(outputDirectory, "ort/ort-wasm-simd-threaded.mjs")),
    copyFile(join(here, "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm"), join(outputDirectory, "ort/ort-wasm-simd-threaded.wasm")),
    copyFile(join(here, "node_modules/js-yaml/LICENSE"), join(outputDirectory, "licenses/js-yaml-MIT.txt")),
    copyFile(join(here, "THIRD_PARTY_NOTICES.md"), join(outputDirectory, "licenses/THIRD_PARTY_NOTICES.md")),
    writeFile(
      join(outputDirectory, "models/korean-recognition-dictionary.json"),
      `${JSON.stringify([...dictionary, " "])}\n`,
      { flag: "wx" },
    ),
  ]);
  await build({
    entryPoints: [join(here, "src/direct-ocr-worker.js")],
    bundle: true,
    format: "iife",
    platform: "browser",
    outfile: join(outputDirectory, "direct/ocr-worker.js"),
    logLevel: "silent",
  });

  assertLayout(await listFiles(outputDirectory), payloadFiles, "payload");
  const files = {};
  for (const name of payloadFiles) {
    const absolute = join(outputDirectory, name);
    const bytes = await readFile(absolute);
    files[name] = { sha256: sha256(bytes), size_bytes: (await stat(absolute)).size };
  }
  const runtimeManifest = {
    schema_version: 1,
    model_id: modelManifest.model_id,
    source_manifest_sha256: sha256(modelManifestBytes),
    runtime: modelManifest.runtime,
    files,
  };
  await writeFile(
    join(outputDirectory, "runtime-manifest.json"),
    `${JSON.stringify(runtimeManifest, null, 2)}\n`,
    { flag: "wx" },
  );
  assertLayout(await listFiles(outputDirectory), finalFiles, "final");
  process.stdout.write(`${JSON.stringify(runtimeManifest)}\n`);
} finally {
  await rm(unpackRoot, { recursive: true, force: true });
}
