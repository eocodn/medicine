import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, rename, rm, stat } from "node:fs/promises";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

import { acquireAdvisoryLock, releaseAdvisoryLock } from "../advisory_lock.mts";
import { loadDetectorModelManifest } from "./detector_models.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCK_FILE = ".asset-fetch.lock";

function run(command, args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolvePromise(Buffer.concat(stdout).toString("utf8"));
      else reject(new Error(`${command} exited ${code}: ${Buffer.concat(stderr).toString("utf8").trim()}`));
    });
  });
}

async function maybeStat(path) {
  try {
    return await stat(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function sha256File(path) {
  const hash = createHash("sha256");
  await new Promise((resolvePromise, reject) => {
    const stream = createReadStream(path);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", resolvePromise);
    stream.on("error", reject);
  });
  return hash.digest("hex");
}

async function downloadPinned(modelName, model, archivePath) {
  const partialPath = `${archivePath}.partial`;
  let partial = await maybeStat(partialPath);
  let offset = partial?.size || 0;
  const headers = offset ? { Range: `bytes=${offset}-` } : {};
  let response = await fetch(model.url, { headers });
  if (offset && response.status === 200) {
    await rm(partialPath, { force: true });
    offset = 0;
    response = await fetch(model.url);
  }
  if (!(response.status === 200 || response.status === 206) || !response.body) {
    throw new Error(`${modelName}: HTTP ${response.status}`);
  }
  if (offset && response.status === 206) {
    const contentRange = response.headers.get("content-range") || "";
    if (!contentRange.startsWith(`bytes ${offset}-`)) throw new Error(`${modelName}: invalid Content-Range ${contentRange}`);
  }
  const remaining = Number(response.headers.get("content-length")) || null;
  const total = remaining === null ? null : offset + remaining;
  const output = createWriteStream(partialPath, { flags: offset ? "a" : "w" });
  let received = offset;
  let lastReported = -1;
  try {
    for await (const chunk of response.body) {
      if (!output.write(chunk)) await new Promise((resolvePromise) => output.once("drain", resolvePromise));
      received += chunk.byteLength;
      if (total) {
        const percent = Math.floor(received * 20 / total) * 5;
        if (percent !== lastReported) {
          lastReported = percent;
          process.stderr.write(`[det-assets] ${modelName} ${Math.min(100, percent)}%\n`);
        }
      }
    }
  } finally {
    await new Promise((resolvePromise, reject) => output.end((error) => (error ? reject(error) : resolvePromise())));
  }
  if (total !== null && received !== total) throw new Error(`${modelName}: truncated download ${received}/${total}`);
  const digest = await sha256File(partialPath);
  if (digest !== model.sha256) throw new Error(`${modelName}: sha256 mismatch ${digest}`);
  await rename(partialPath, archivePath);
}

function safeArchiveEntries(entries, expectedRoot) {
  const prefix = `${expectedRoot}/`;
  for (const entry of entries) {
    if (!entry || entry.startsWith("/") || entry.includes("../") || !(entry === expectedRoot || entry.startsWith(prefix))) {
      throw new Error(`unsafe detector archive entry: ${entry}`);
    }
  }
}

async function extractPinned(modelName, model, archivePath, outputDir) {
  const extracted = join(outputDir, model.archive_root);
  const onnx = join(extracted, model.onnx_file);
  const config = join(extracted, model.config_file);
  if ((await maybeStat(onnx))?.isFile() && (await maybeStat(config))?.isFile()) return extracted;

  const listing = (await run("tar", ["-tf", archivePath])).split(/\r?\n/).filter(Boolean);
  safeArchiveEntries(listing, model.archive_root);
  const temporary = join(outputDir, `.extract-${modelName}-${process.pid}`);
  await rm(temporary, { recursive: true, force: true });
  await mkdir(temporary, { recursive: true });
  try {
    await run("tar", ["-xf", archivePath, "-C", temporary]);
    const candidate = join(temporary, model.archive_root);
    if (!(await maybeStat(join(candidate, model.onnx_file)))?.isFile()) throw new Error(`${modelName}: ONNX file missing after extraction`);
    if (!(await maybeStat(join(candidate, model.config_file)))?.isFile()) throw new Error(`${modelName}: inference config missing after extraction`);
    await rm(extracted, { recursive: true, force: true });
    await rename(candidate, extracted);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
  return extracted;
}

export async function fetchDetectorAssets({ outputDir, modelNames = null }) {
  const root = resolve(outputDir);
  await mkdir(root, { recursive: true });
  const lockPath = join(root, LOCK_FILE);
  const lock = await acquireAdvisoryLock(lockPath, {
    busyMessage: "detector asset fetch is already running for this output directory",
    label: "detector asset fetch lock",
  });
  try {
    const manifest = await loadDetectorModelManifest();
    const requested = modelNames || Object.keys(manifest.models);
    const results = [];
    for (const modelName of requested) {
      const model = manifest.models[modelName];
      if (!model) throw new Error(`unknown detector model ${modelName}`);
      const archivePath = join(root, model.archive);
      const existing = await maybeStat(archivePath);
      if (!existing) await downloadPinned(modelName, model, archivePath);
      else {
        const digest = await sha256File(archivePath);
        if (digest !== model.sha256) throw new Error(`${modelName}: cached archive sha256 mismatch`);
      }
      const extracted = await extractPinned(modelName, model, archivePath, root);
      const onnxPath = join(extracted, model.onnx_file);
      results.push({
        model: modelName,
        archive: archivePath,
        archive_sha256: model.sha256,
        onnx: onnxPath,
        model_bytes: (await stat(onnxPath)).size,
        config: join(extracted, model.config_file),
      });
    }
    return { schema_version: 1, output_dir: root, models: results };
  } finally {
    await releaseAdvisoryLock(lock);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const output = process.argv[2] || join(HERE, ".cache", "models");
  fetchDetectorAssets({ outputDir: output }).then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
  }).catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 2;
  });
}