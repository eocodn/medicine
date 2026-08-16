import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, copyFile, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { RUNTIME_FILES, RUNTIME_PAYLOAD_FILES } from "../runtime-layout.mjs";

const run = promisify(execFile);
const [downloadDirectory, outputDirectory] = process.argv.slice(2);
if (!downloadDirectory || !outputDirectory) throw new Error("usage: node mobile/export_runtime.mjs DOWNLOAD_DIR OUTPUT_DIR");

const mobileRoot = dirname(fileURLToPath(import.meta.url));
const browserRoot = dirname(mobileRoot);
const sourceManifest = JSON.parse(await readFile(join(browserRoot, "model-manifest.json"), "utf8"));
const mobileManifestBytes = await readFile(join(mobileRoot, "model-manifest.json"));
const mobileManifest = JSON.parse(mobileManifestBytes.toString("utf8"));
const detectorArchive = sourceManifest.sources?.detection?.archive;
if (!detectorArchive || detectorArchive !== mobileManifest.detector_source?.archive) {
  throw new Error("mobile detector provenance does not match the pinned source manifest");
}

function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
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
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) throw new Error(`${phase} runtime layout mismatch`);
}
async function assertPinned(path, expected, label) {
  const bytes = await readFile(path);
  const digest = sha256(bytes);
  if (digest !== expected) throw new Error(`${label} sha256 mismatch`);
  return bytes;
}

const unpackRoot = await mkdtemp(join(tmpdir(), "medicine-mobile-ocr-"));
const detectionDirectory = join(unpackRoot, "detection");
await mkdir(detectionDirectory);
try {
  await run("tar", ["-xf", join(downloadDirectory, detectorArchive), "-C", detectionDirectory, "--strip-components=1"]);
  const detector = await assertPinned(
    join(downloadDirectory, detectorArchive), mobileManifest.detector_source.sha256, "detector archive",
  );
  const recognizer = await assertPinned(
    join(mobileRoot, "korean-recognition.onnx"), mobileManifest.selected_recognizer.onnx_sha256, "recognizer",
  );
  const dictionary = await assertPinned(
    join(mobileRoot, "korean-recognition-dictionary.json"), mobileManifest.selected_recognizer.dictionary_sha256, "dictionary",
  );
  if (recognizer.length !== mobileManifest.selected_recognizer.onnx_size_bytes) throw new Error("recognizer size mismatch");
  void detector;

  await rm(outputDirectory, { recursive: true, force: true });
  for (const directory of ["direct", "models", "ort", "licenses"]) await mkdir(join(outputDirectory, directory), { recursive: true });
  await Promise.all([
    copyFile(join(detectionDirectory, "inference.onnx"), join(outputDirectory, "models/detection.onnx")),
    writeFile(join(outputDirectory, "models/korean-recognition.onnx"), recognizer, { flag: "wx" }),
    writeFile(join(outputDirectory, "models/korean-recognition-dictionary.json"), dictionary, { flag: "wx" }),
    copyFile(join(browserRoot, "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs"), join(outputDirectory, "ort/ort-wasm-simd-threaded.mjs")),
    copyFile(join(browserRoot, "node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm"), join(outputDirectory, "ort/ort-wasm-simd-threaded.wasm")),
    copyFile(join(browserRoot, "node_modules/js-yaml/LICENSE"), join(outputDirectory, "licenses/js-yaml-MIT.txt")),
    copyFile(join(browserRoot, "THIRD_PARTY_NOTICES.md"), join(outputDirectory, "licenses/THIRD_PARTY_NOTICES.md")),
  ]);
  await build({
    entryPoints: [join(browserRoot, "src/direct-ocr-worker.js")], bundle: true, format: "iife", platform: "browser",
    outfile: join(outputDirectory, "direct/ocr-worker.js"), logLevel: "silent",
  });
  for (const name of RUNTIME_PAYLOAD_FILES) await chmod(join(outputDirectory, name), 0o644);
  assertLayout(await listFiles(outputDirectory), RUNTIME_PAYLOAD_FILES, "payload");
  const files = {};
  for (const name of RUNTIME_PAYLOAD_FILES) {
    const bytes = await readFile(join(outputDirectory, name));
    files[name] = { sha256: sha256(bytes), size_bytes: (await stat(join(outputDirectory, name))).size };
  }
  const runtimeManifest = {
    schema_version: 1,
    model_id: mobileManifest.model_id,
    source_manifest_sha256: sha256(mobileManifestBytes),
    selected_checkpoint_sha256: mobileManifest.selected_recognizer.checkpoint_sha256,
    runtime: mobileManifest.runtime,
    files,
  };
  await writeFile(join(outputDirectory, "runtime-manifest.json"), `${JSON.stringify(runtimeManifest, null, 2)}\n`, { flag: "wx" });
  assertLayout(await listFiles(outputDirectory), RUNTIME_FILES, "final");
  process.stdout.write(`${JSON.stringify(runtimeManifest)}\n`);
} finally {
  await rm(unpackRoot, { recursive: true, force: true });
}
