#!/usr/bin/env node

import { writeFile } from "node:fs/promises";

function option(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index < 0 ? fallback : process.argv[index + 1];
}

const debuggerUrl = option("--debugger-url", "http://127.0.0.1:9222");
const targetUrl = option("--url");
const screenshotPath = option("--screenshot");
const timeoutMs = Number(option("--timeout-ms", "30000"));
const jsonOutput = process.argv.includes("--json");

if (!targetUrl || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
  console.error("usage: probe_browser.mjs --url URL [--debugger-url URL] [--timeout-ms N] [--screenshot PATH] [--json]");
  process.exit(2);
}

const target = await fetch(`${debuggerUrl}/json/new?${encodeURIComponent(targetUrl)}`, {
  method: "PUT",
}).then((response) => {
  if (!response.ok) throw new Error(`could not create browser target: HTTP ${response.status}`);
  return response.json();
});

const socket = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 1;

socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
});

await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", () => reject(new Error("browser WebSocket failed")), { once: true });
});

function command(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function title() {
  const result = await command("Runtime.evaluate", {
    expression: "document.title",
    returnByValue: true,
  });
  return result.result.value;
}

async function closeTarget() {
  socket.close();
  await fetch(`${debuggerUrl}/json/close/${encodeURIComponent(target.id)}`).catch(() => null);
}

const startedAt = Date.now();
let finalTitle = "";
while (Date.now() - startedAt < timeoutMs) {
  finalTitle = await title();
  if (finalTitle.startsWith("OCR_OK ") || finalTitle.startsWith("OCR_FAIL ")) break;
  await new Promise((resolve) => setTimeout(resolve, 50));
}

if (!finalTitle.startsWith("OCR_OK ") && !finalTitle.startsWith("OCR_FAIL ")) {
  await closeTarget();
  throw new Error(`OCR probe timed out after ${timeoutMs} ms (last title: ${finalTitle})`);
}

if (screenshotPath) {
  const screenshot = await command("Page.captureScreenshot", { format: "png" });
  await writeFile(screenshotPath, Buffer.from(screenshot.data, "base64"));
}

const separator = finalTitle.indexOf(" ");
const status = finalTitle.slice(0, separator);
const detail = JSON.parse(finalTitle.slice(separator + 1));
const result = { status, wall_ms: Date.now() - startedAt, detail };
await closeTarget();
console.log(jsonOutput ? JSON.stringify(result) : `${status}: ${JSON.stringify(detail, null, 2)}`);

if (status !== "OCR_OK") process.exitCode = 1;
