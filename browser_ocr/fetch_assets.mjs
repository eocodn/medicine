import { createHash } from "node:crypto";
import { readFile, mkdir, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const outputDirectory = process.argv[2];
const requestedSourceNames = process.argv.slice(3);
if (!outputDirectory) throw new Error("usage: node fetch_assets.mjs OUTPUT_DIRECTORY [SOURCE_NAME ...]");

const here = dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(await readFile(join(here, "model-manifest.json"), "utf8"));
const sources = manifest.sources || {};
const sourceNames = requestedSourceNames.length ? requestedSourceNames : Object.keys(sources);
const unknownSourceNames = sourceNames.filter((name) => !Object.hasOwn(sources, name));
if (unknownSourceNames.length) throw new Error(`unknown model source: ${unknownSourceNames.join(", ")}`);
const assets = sourceNames.map((name) => sources[name]);
if (manifest.schema_version !== 1 || assets.length === 0) {
  throw new Error("model-manifest.json is missing supported pinned sources");
}

await mkdir(outputDirectory, { recursive: true });

for (const asset of assets) {
  if (!asset?.archive || !asset?.url || !/^[0-9a-f]{64}$/.test(asset.sha256 || "")) {
    throw new Error("model-manifest.json contains an invalid source entry");
  }
  const response = await fetch(asset.url);
  if (!response.ok || !response.body) throw new Error(`${asset.archive}: HTTP ${response.status}`);
  const total = Number(response.headers.get("content-length")) || null;
  const chunks = [];
  let received = 0;
  let reported = -1;
  for await (const chunk of response.body) {
    chunks.push(chunk);
    received += chunk.byteLength;
    const percent = total ? Math.floor((received / total) * 10) * 10 : null;
    if (percent !== null && percent !== reported) {
      reported = percent;
      process.stderr.write(`${asset.archive}: ${Math.min(percent, 100)}%\n`);
    }
  }
  if (total !== null && received !== total) throw new Error(`${asset.archive}: truncated download`);
  const bytes = Buffer.concat(chunks);
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== asset.sha256) throw new Error(`${asset.archive}: sha256 mismatch`);
  const target = join(outputDirectory, asset.archive);
  const temporary = `${target}.partial`;
  await rm(temporary, { force: true });
  await writeFile(temporary, bytes, { flag: "wx" });
  await rename(temporary, target);
  process.stderr.write(`${asset.archive}: verified ${received} bytes\n`);
}
