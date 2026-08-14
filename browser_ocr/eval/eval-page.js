"use strict";

function seedFromText(value) {
  let seed = 2166136261;
  for (const character of String(value || "")) {
    seed ^= character.codePointAt(0);
    seed = Math.imul(seed, 16777619);
  }
  return seed >>> 0;
}

function createRandom(seed) {
  let state = seed || 1;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

async function decodedImage(blob) {
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = "sync";
    image.src = url;
    await image.decode();
    return image;
  } finally {
    // The decoded image retains its pixels after decode in Chromium.
    URL.revokeObjectURL(url);
  }
}

function addDeterministicNoise(context, width, height, amount, seed) {
  if (!(amount > 0)) return;
  const image = context.getImageData(0, 0, width, height);
  const random = createRandom(seed);
  const magnitude = amount * 255;
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const delta = (random() * 2 - 1) * magnitude;
    image.data[offset] = Math.max(0, Math.min(255, image.data[offset] + delta));
    image.data[offset + 1] = Math.max(0, Math.min(255, image.data[offset + 1] + delta));
    image.data[offset + 2] = Math.max(0, Math.min(255, image.data[offset + 2] + delta));
  }
  context.putImageData(image, 0, 0);
}

async function prepareForWorker(blob, transform = null, seedText = "") {
  const needsRaster = blob.type === "image/svg+xml" || transform;
  if (!needsRaster) return blob;

  const image = await decodedImage(blob);
  const settings = transform || {};
  const scale = Number(settings.scale ?? 1);
  const angle = Number(settings.rotation_degrees ?? 0) * Math.PI / 180;
  const sourceWidth = Math.max(1, Math.round(image.naturalWidth * scale));
  const sourceHeight = Math.max(1, Math.round(image.naturalHeight * scale));
  const cosine = Math.abs(Math.cos(angle));
  const sine = Math.abs(Math.sin(angle));
  const width = Math.max(1, Math.ceil(sourceWidth * cosine + sourceHeight * sine));
  const height = Math.max(1, Math.ceil(sourceWidth * sine + sourceHeight * cosine));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: Number(settings.noise || 0) > 0 });
  if (!context) throw new Error("2D canvas unavailable for OCR evaluation");
  context.fillStyle = "white";
  context.fillRect(0, 0, width, height);
  const filters = [];
  if (settings.blur_px !== undefined) filters.push(`blur(${Number(settings.blur_px)}px)`);
  if (settings.brightness !== undefined) filters.push(`brightness(${Number(settings.brightness)})`);
  if (settings.contrast !== undefined) filters.push(`contrast(${Number(settings.contrast)})`);
  context.filter = filters.length ? filters.join(" ") : "none";
  context.translate(width / 2, height / 2);
  context.rotate(angle);
  context.drawImage(image, -sourceWidth / 2, -sourceHeight / 2, sourceWidth, sourceHeight);
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.filter = "none";
  addDeterministicNoise(context, width, height, Number(settings.noise || 0), seedFromText(seedText));
  return await new Promise((resolve, reject) => canvas.toBlob(
    (png) => png ? resolve(png) : reject(new Error("could not rasterize OCR evaluation image")),
    "image/png",
  ));
}

(async () => {
  const corpus = await fetch("/corpus/manifest.json").then((response) => {
    if (!response.ok) throw new Error(`corpus HTTP ${response.status}`);
    return response.json();
  });
  const state = {
    status: "running",
    completed: 0,
    total: Array.isArray(corpus.samples) ? corpus.samples.length : 0,
    current: null,
    samples: [],
    error: null,
  };
  window.__OCR_EVAL_STATE__ = state;

  for (const sample of corpus.samples || []) {
    state.current = sample.id;
    const response = await fetch(`/corpus/${sample.image}`);
    if (!response.ok) throw new Error(`${sample.id}: image HTTP ${response.status}`);
    const image = await prepareForWorker(await response.blob(), sample.transform, sample.id);
    const worker = new Worker("/ocr-assets/direct/ocr-worker.js", { type: "classic" });
    const started = performance.now();
    const result = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`${sample.id}: OCR timed out`)), 120000);
      worker.onmessage = (event) => {
        const message = event.data || {};
        if (message.type === "result") {
          clearTimeout(timeout);
          resolve(message.items || []);
        } else if (message.type === "error") {
          clearTimeout(timeout);
          reject(new Error(`${sample.id}: ${message.error || "OCR worker failed"}`));
        }
      };
      worker.onerror = () => {
        clearTimeout(timeout);
        reject(new Error(`${sample.id}: OCR worker crashed`));
      };
      worker.postMessage({ type: "recognize", image });
    }).finally(() => worker.terminate());
    state.samples.push({ id: sample.id, wall_ms: Math.round(performance.now() - started), items: result });
    state.completed = state.samples.length;
  }
  state.current = null;
  state.status = "done";
  document.title = "OCR_EVAL_DONE";
})().catch((error) => {
  const state = window.__OCR_EVAL_STATE__ || { completed: 0, total: 0, samples: [] };
  state.status = "failed";
  state.error = error instanceof Error ? error.message : String(error);
  state.current = null;
  window.__OCR_EVAL_STATE__ = state;
  document.title = "OCR_EVAL_FAILED";
});
