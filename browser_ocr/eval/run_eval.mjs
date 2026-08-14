import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateCorpus, validateCorpus } from "./metrics.mjs";

function option(name, fallback = null) {
  const index = process.argv.lastIndexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
}

const here = dirname(fileURLToPath(import.meta.url));
const runtimeDirectory = resolve(option("--runtime-dir", "/ocr-assets"));
const corpusPath = resolve(option("--corpus", join(here, "corpus/manifest.json")));
const chromium = option("--chromium", process.env.CHROMIUM || "chromium");
const reportPath = option("--report");
const checkpointPath = option("--checkpoint", reportPath ? `${reportPath}.partial` : null);
const timeoutMs = Number(option("--timeout-ms", "240000"));
const jsonOutput = process.argv.includes("--json");
if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("--timeout-ms must be positive");

const corpusRoot = dirname(corpusPath);
const corpus = validateCorpus(JSON.parse(await readFile(corpusPath, "utf8")));
const runtimeManifest = JSON.parse(await readFile(join(runtimeDirectory, "runtime-manifest.json"), "utf8"));

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
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".onnx": "application/octet-stream",
  ".wasm": "application/wasm",
  ".mjs": "text/javascript; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

async function sendFile(response, absolute) {
  const info = await stat(absolute);
  if (!info.isFile()) throw new Error("not a file");
  response.writeHead(200, { "content-type": MIME[extname(absolute).toLowerCase()] || "application/octet-stream" });
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

async function writeCheckpoint(state) {
  if (!checkpointPath) return;
  await writeFile(checkpointPath, `${JSON.stringify(state, null, 2)}\n`);
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
  let lastCompleted = -1;
  while (Date.now() < deadline) {
    const evaluated = await command("Runtime.evaluate", {
      expression: "JSON.stringify(window.__OCR_EVAL_STATE__ || null)",
      returnByValue: true,
    });
    const value = evaluated.result?.value;
    if (value && value !== "null") {
      const state = JSON.parse(value);
      if (Number(state.completed) !== lastCompleted) {
        lastCompleted = Number(state.completed);
        process.stderr.write(`[ocr-eval] ${state.completed}/${state.total}${state.current ? ` ${state.current}` : ""}\n`);
        await writeCheckpoint(state);
      }
      if (state.status === "done") {
        rawResult = { samples: state.samples || [] };
        await writeCheckpoint(state);
        break;
      }
      if (state.status === "failed") throw new Error(state.error || "OCR evaluation failed");
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
  }
  socket.close();
  if (!rawResult) throw new Error(`OCR evaluation timed out after ${timeoutMs} ms`);
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

const evaluated = evaluateCorpus(corpus, rawResult);
const report = {
  schema_version: 2,
  status: evaluated.status,
  model_id: runtimeManifest.model_id,
  runtime_manifest: basename(join(runtimeDirectory, "runtime-manifest.json")),
  source_manifest_sha256: runtimeManifest.source_manifest_sha256,
  corpus_id: corpus.corpus_id,
  gates: evaluated.gates,
  metrics: evaluated.metrics,
  scenarios: evaluated.scenarios,
  samples: evaluated.samples,
};
if (reportPath) await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`);
process.stdout.write(jsonOutput ? `${JSON.stringify(report)}\n` : `${JSON.stringify(report, null, 2)}\n`);
if (report.status !== "pass") process.exitCode = 1;
