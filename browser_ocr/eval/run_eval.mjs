import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

function option(name, fallback = null) {
  const index = process.argv.lastIndexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
}

const here = dirname(fileURLToPath(import.meta.url));
const runtimeDirectory = resolve(option("--runtime-dir", "/ocr-assets"));
const corpusPath = resolve(option("--corpus", join(here, "corpus/manifest.json")));
const chromium = option("--chromium", process.env.CHROMIUM || "chromium");
const reportPath = option("--report");
const timeoutMs = Number(option("--timeout-ms", "240000"));
const jsonOutput = process.argv.includes("--json");
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("--timeout-ms must be positive");

const corpusRoot = dirname(corpusPath);
const corpus = JSON.parse(await readFile(corpusPath, "utf8"));
const runtimeManifest = JSON.parse(await readFile(join(runtimeDirectory, "runtime-manifest.json"), "utf8"));
if (corpus.schema_version !== 1 || !Array.isArray(corpus.samples) || !corpus.samples.length) {
  throw new Error("unsupported or empty OCR evaluation corpus");
}

function safePath(root, relativePath) {
  const absolute = resolve(root, relativePath);
  if (absolute !== root && !absolute.startsWith(`${root}${sep}`)) throw new Error("path escapes evaluation root");
  return absolute;
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".onnx": "application/octet-stream",
  ".wasm": "application/wasm",
  ".mjs": "text/javascript; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

async function sendFile(response, absolute) {
  const info = await stat(absolute);
  if (!info.isFile()) throw new Error("not a file");
  response.writeHead(200, { "content-type": MIME[extname(absolute)] || "application/octet-stream" });
  response.end(await readFile(absolute));
}

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (url.pathname === "/" || url.pathname === "/eval") {
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      response.end("<!doctype html><meta charset=\"utf-8\"><title>OCR_EVAL_RUNNING</title><script src=\"/eval.js\" defer></script>");
      return;
    }
    if (url.pathname === "/eval.js") {
      await sendFile(response, join(here, "eval-page.js"));
      return;
    }
    if (url.pathname === "/corpus/manifest.json") {
      await sendFile(response, corpusPath);
      return;
    }
    if (url.pathname.startsWith("/corpus/")) {
      await sendFile(response, safePath(corpusRoot, decodeURIComponent(url.pathname.slice("/corpus/".length))));
      return;
    }
    if (url.pathname.startsWith("/ocr-assets/")) {
      await sendFile(response, safePath(runtimeDirectory, decodeURIComponent(url.pathname.slice("/ocr-assets/".length))));
      return;
    }
    response.writeHead(404).end();
  } catch (error) {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end(error instanceof Error ? error.message : "not found");
  }
});
await new Promise((resolvePromise) => server.listen(0, "127.0.0.1", resolvePromise));
const address = server.address();
if (!address || typeof address === "string") throw new Error("could not bind OCR evaluation server");
const evaluationUrl = `http://127.0.0.1:${address.port}/eval`;

const profile = await mkdtemp(join(tmpdir(), "medicine-ocr-eval-"));
const browser = spawn(chromium, [
  "--headless=new",
  "--no-sandbox",
  "--disable-gpu",
  "--disable-dev-shm-usage",
  "--no-first-run",
  "--no-default-browser-check",
  `--user-data-dir=${profile}`,
  "--remote-debugging-port=0",
  "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
let browserStderr = "";
browser.stderr.on("data", (chunk) => { browserStderr += chunk.toString(); });

async function waitForDebugger() {
  const activePort = join(profile, "DevToolsActivePort");
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const [port] = (await readFile(activePort, "utf8")).trim().split(/\r?\n/);
      if (port) return Number(port);
    } catch (_) {}
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50));
  }
  throw new Error(`Chromium debugger did not start: ${browserStderr.slice(-1000)}`);
}

async function openTarget(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(evaluationUrl)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`could not open OCR evaluation target: HTTP ${response.status}`);
  return response.json();
}

function cdp(socket) {
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve: resolvePromise, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolvePromise(message.result);
  });
  return (method, params = {}) => new Promise((resolvePromise, reject) => {
    const id = nextId++;
    pending.set(id, { resolve: resolvePromise, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

let rawResult;
try {
  const debuggerPort = await waitForDebugger();
  const target = await openTarget(debuggerPort);
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolvePromise, reject) => {
    socket.addEventListener("open", resolvePromise, { once: true });
    socket.addEventListener("error", () => reject(new Error("Chromium WebSocket failed")), { once: true });
  });
  const command = cdp(socket);
  await command("Runtime.enable");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const evaluated = await command("Runtime.evaluate", {
      expression: "JSON.stringify(window.__OCR_EVAL_RESULT__ || null)",
      returnByValue: true,
    });
    const value = evaluated.result?.value;
    if (value && value !== "null") {
      rawResult = JSON.parse(value);
      break;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
  }
  socket.close();
  if (!rawResult) throw new Error(`OCR evaluation timed out after ${timeoutMs} ms`);
  if (rawResult.error) throw new Error(rawResult.error);
} finally {
  const exited = new Promise((resolvePromise) => {
    if (browser.exitCode !== null) resolvePromise();
    else browser.once("exit", resolvePromise);
  });
  browser.kill("SIGTERM");
  await Promise.race([exited, new Promise((resolvePromise) => setTimeout(resolvePromise, 5000))]);
  await new Promise((resolvePromise) => server.close(resolvePromise));
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

function normalized(value) {
  return String(value || "").normalize("NFC").replace(/\s+/gu, "").trim();
}

function editDistance(left, right) {
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index);
  for (let i = 1; i <= left.length; i += 1) {
    let diagonal = previous[0];
    previous[0] = i;
    for (let j = 1; j <= right.length; j += 1) {
      const above = previous[j];
      previous[j] = Math.min(
        previous[j] + 1,
        previous[j - 1] + 1,
        diagonal + (left[i - 1] === right[j - 1] ? 0 : 1),
      );
      diagonal = above;
    }
  }
  return previous[right.length];
}

const byId = new Map(rawResult.samples.map((sample) => [sample.id, sample]));
let totalExpectedCharacters = 0;
let totalCharacterErrors = 0;
let criticalTotal = 0;
let criticalFound = 0;
let numericTotal = 0;
let numericFound = 0;
let maxSampleCer = 0;
const sampleResults = [];
for (const expected of corpus.samples) {
  const observed = byId.get(expected.id);
  if (!observed) throw new Error(`missing OCR result for ${expected.id}`);
  const recognizedText = observed.items.map((item) => item.text).join(" ");
  const expectedCompact = normalized(expected.expected_text);
  const recognizedCompact = normalized(recognizedText);
  const errors = editDistance(expectedCompact, recognizedCompact);
  const cer = expectedCompact.length ? errors / expectedCompact.length : 0;
  maxSampleCer = Math.max(maxSampleCer, cer);
  totalExpectedCharacters += expectedCompact.length;
  totalCharacterErrors += errors;
  const critical = expected.critical_tokens || [];
  const foundCritical = critical.filter((token) => recognizedCompact.includes(normalized(token)));
  criticalTotal += critical.length;
  criticalFound += foundCritical.length;
  const numeric = Array.isArray(expected.numeric_tokens) ? expected.numeric_tokens : [];
  const foundNumeric = numeric.filter((token) => recognizedCompact.includes(normalized(token)));
  numericTotal += numeric.length;
  numericFound += foundNumeric.length;
  const scores = observed.items.map((item) => Number(item.score)).filter(Number.isFinite);
  sampleResults.push({
    id: expected.id,
    wall_ms: observed.wall_ms,
    expected_text: expected.expected_text,
    recognized_text: recognizedText,
    character_error_rate: cer,
    critical_tokens_found: foundCritical,
    critical_tokens_total: critical.length,
    numeric_tokens_found: foundNumeric,
    numeric_tokens_total: numeric.length,
    mean_token_score: scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null,
    items: observed.items,
  });
}

const metrics = {
  sample_count: sampleResults.length,
  character_error_rate: totalExpectedCharacters ? totalCharacterErrors / totalExpectedCharacters : 0,
  max_sample_character_error_rate: maxSampleCer,
  critical_token_recall: criticalTotal ? criticalFound / criticalTotal : 1,
  numeric_token_recall: numericTotal ? numericFound / numericTotal : 1,
  wall_ms: sampleResults.reduce((sum, sample) => sum + sample.wall_ms, 0),
};
const gates = corpus.gates || {};
const passed = metrics.max_sample_character_error_rate <= Number(gates.max_character_error_rate)
  && metrics.critical_token_recall >= Number(gates.critical_token_recall)
  && metrics.numeric_token_recall >= Number(gates.numeric_token_recall);
const report = {
  schema_version: 1,
  status: passed ? "pass" : "fail",
  model_id: runtimeManifest.model_id,
  runtime_manifest: basename(join(runtimeDirectory, "runtime-manifest.json")),
  source_manifest_sha256: runtimeManifest.source_manifest_sha256,
  corpus_id: corpus.corpus_id,
  gates,
  metrics,
  samples: sampleResults,
};
if (reportPath) await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(jsonOutput ? `${JSON.stringify(report)}\n` : `${JSON.stringify(report, null, 2)}\n`);
if (!passed) process.exitCode = 1;
