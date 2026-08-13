"use strict";

async function rasterizeForWorker(blob) {
  if (blob.type !== "image/svg+xml") return blob;
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = "sync";
    image.src = url;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("2D canvas unavailable for synthetic OCR corpus");
    context.fillStyle = "white";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0);
    return await new Promise((resolve, reject) => canvas.toBlob(
      (png) => png ? resolve(png) : reject(new Error("could not rasterize synthetic OCR corpus image")),
      "image/png",
    ));
  } finally {
    URL.revokeObjectURL(url);
  }
}

(async () => {
  const corpus = await fetch("/corpus/manifest.json").then((response) => {
    if (!response.ok) throw new Error(`corpus HTTP ${response.status}`);
    return response.json();
  });
  const samples = [];
  for (const sample of corpus.samples || []) {
    const response = await fetch(`/corpus/${sample.image}`);
    if (!response.ok) throw new Error(`${sample.id}: image HTTP ${response.status}`);
    const image = await rasterizeForWorker(await response.blob());
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
    samples.push({ id: sample.id, wall_ms: Math.round(performance.now() - started), items: result });
  }
  window.__OCR_EVAL_RESULT__ = { samples };
  document.title = "OCR_EVAL_DONE";
})().catch((error) => {
  window.__OCR_EVAL_RESULT__ = { error: error instanceof Error ? error.message : String(error) };
  document.title = "OCR_EVAL_FAILED";
});
