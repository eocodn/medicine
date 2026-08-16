"use strict";

const ort = require("onnxruntime-web/wasm");
const {
  decodeCtc,
  decodeDetectionMap,
  distance,
  foregroundColumnInk,
  horizontalSubpolygon,
  resizeWithin,
  rgbaToChw,
  splitHorizontalInkRanges,
} = require("./direct-ocr-core.js");
const { parseDocumentItems } = require("./document-parser.js");

const DETECTION_MODEL = "/ocr-assets/models/detection.onnx";
const RECOGNITION_MODEL = "/ocr-assets/models/korean-recognition.onnx";
const RECOGNITION_DICTIONARY = "/ocr-assets/models/korean-recognition-dictionary.json";
const MAX_SOURCE_EDGE = 1280;
const DETECTION_EDGE = 640;
const RECOGNITION_HEIGHT = 48;
const RECOGNITION_BASE_WIDTH = 320;
const RECOGNITION_MAX_WIDTH = 3200;
const RECOGNITION_BATCH_SIZE = 4;
const DETECTION_NORMALIZE = {
  mean: [0.485, 0.456, 0.406],
  standardDeviation: [0.229, 0.224, 0.225],
};
const RECOGNITION_NORMALIZE = {
  mean: [0.5, 0.5, 0.5],
  standardDeviation: [0.5, 0.5, 0.5],
};

ort.env.wasm.wasmPaths = "/ocr-assets/ort/";
ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;

let running = false;
let detectionSession = null;
let recognitionSession = null;

function progress(value) {
  self.postMessage({ type: "progress", progress: value });
}

function createCanvas(width, height) {
  if (typeof OffscreenCanvas !== "function") {
    throw new Error("OffscreenCanvas is required for direct browser OCR");
  }
  return new OffscreenCanvas(width, height);
}

function context2d(canvas) {
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Canvas 2D context is unavailable");
  return context;
}

function drawScaled(source, width, height) {
  const canvas = createCanvas(width, height);
  const context = context2d(canvas);
  context.fillStyle = "#fff";
  context.fillRect(0, 0, width, height);
  context.drawImage(source, 0, 0, width, height);
  return canvas;
}

function detectionDimensions(width, height) {
  const resized = resizeWithin(width, height, DETECTION_EDGE);
  return {
    width: Math.max(32, Math.round(resized.width / 32) * 32),
    height: Math.max(32, Math.round(resized.height / 32) * 32),
  };
}

function tensorFromCanvas(canvas, normalize) {
  const image = context2d(canvas).getImageData(0, 0, canvas.width, canvas.height);
  return new ort.Tensor(
    "float32",
    rgbaToChw(image.data, canvas.width, canvas.height, normalize.mean, normalize.standardDeviation),
    [1, 3, canvas.height, canvas.width],
  );
}

async function runSession(session, tensor) {
  const output = await session.run({ [session.inputNames[0]]: tensor });
  return output[session.outputNames[0]];
}

function cropRotated(source, polygon) {
  const width = Math.max(1, Math.floor(Math.max(
    distance(polygon[0], polygon[1]), distance(polygon[2], polygon[3]),
  )));
  const height = Math.max(1, Math.floor(Math.max(
    distance(polygon[0], polygon[3]), distance(polygon[1], polygon[2]),
  )));
  const angle = Math.atan2(
    polygon[1][1] - polygon[0][1],
    polygon[1][0] - polygon[0][0],
  );
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const crop = createCanvas(width, height);
  const context = context2d(crop);
  context.setTransform(
    cosine,
    -sine,
    sine,
    cosine,
    -(cosine * polygon[0][0] + sine * polygon[0][1]),
    sine * polygon[0][0] - cosine * polygon[0][1],
  );
  context.drawImage(source, 0, 0);
  context.resetTransform();
  if (height / width < 1.5) return crop;

  const rotated = createCanvas(height, width);
  const rotatedContext = context2d(rotated);
  rotatedContext.setTransform(0, -1, 1, 0, 0, width);
  rotatedContext.drawImage(crop, 0, 0);
  rotatedContext.resetTransform();
  crop.width = 1;
  crop.height = 1;
  return rotated;
}

function isTallPolygon(polygon) {
  const width = Math.max(distance(polygon[0], polygon[1]), distance(polygon[2], polygon[3]));
  const height = Math.max(distance(polygon[0], polygon[3]), distance(polygon[1], polygon[2]));
  return height / Math.max(width, 1e-9) >= 1.5;
}

function sliceCrop(crop, start, end) {
  const width = end - start;
  const result = createCanvas(width, crop.height);
  context2d(result).drawImage(crop, start, 0, width, crop.height, 0, 0, width, crop.height);
  return result;
}

function refineDetectedBoxes(sourceCanvas, boxes) {
  const refined = [];
  for (const box of boxes) {
    const crop = cropRotated(sourceCanvas, box.poly);
    let ranges = [[0, crop.width]];
    if (!isTallPolygon(box.poly) && crop.width > 1) {
      const image = context2d(crop).getImageData(0, 0, crop.width, crop.height);
      ranges = splitHorizontalInkRanges(foregroundColumnInk(image), crop.height);
    }
    for (const [start, end] of ranges) {
      const poly = start === 0 && end === crop.width
        ? box.poly : horizontalSubpolygon(box.poly, start, end, crop.width);
      refined.push({ poly, score: box.score, crop: sliceCrop(crop, start, end) });
    }
    crop.width = 1;
    crop.height = 1;
  }
  return refined;
}

function prepareRecognitionSample(crop, inputIndex) {
  const ratio = crop.width / Math.max(1, crop.height);
  const width = Math.min(
    RECOGNITION_MAX_WIDTH,
    Math.max(RECOGNITION_BASE_WIDTH, Math.trunc(RECOGNITION_HEIGHT * ratio)),
  );
  const resizedWidth = Math.min(width, Math.ceil(RECOGNITION_HEIGHT * ratio));
  const canvas = createCanvas(width, RECOGNITION_HEIGHT);
  const context = context2d(canvas);
  // Zero-valued normalized padding corresponds to mid-gray source pixels.
  context.fillStyle = "rgb(128, 128, 128)";
  context.fillRect(0, 0, width, RECOGNITION_HEIGHT);
  context.drawImage(crop, 0, 0, resizedWidth, RECOGNITION_HEIGHT);
  const image = context.getImageData(0, 0, width, RECOGNITION_HEIGHT);
  const chw = rgbaToChw(
    image.data,
    width,
    RECOGNITION_HEIGHT,
    RECOGNITION_NORMALIZE.mean,
    RECOGNITION_NORMALIZE.standardDeviation,
  );
  canvas.width = 1;
  canvas.height = 1;
  crop.width = 1;
  crop.height = 1;
  return { inputIndex, width, chw };
}

function recognitionBatchTensor(samples) {
  const maxWidth = Math.max(...samples.map((sample) => sample.width));
  const plane = RECOGNITION_HEIGHT * maxWidth;
  const output = new Float32Array(samples.length * 3 * plane);
  for (let batch = 0; batch < samples.length; batch += 1) {
    const sample = samples[batch];
    const sourcePlane = RECOGNITION_HEIGHT * sample.width;
    for (let channel = 0; channel < 3; channel += 1) {
      for (let row = 0; row < RECOGNITION_HEIGHT; row += 1) {
        const source = channel * sourcePlane + row * sample.width;
        const target = batch * 3 * plane + channel * plane + row * maxWidth;
        output.set(sample.chw.subarray(source, source + sample.width), target);
      }
    }
  }
  return new ort.Tensor("float32", output, [samples.length, 3, RECOGNITION_HEIGHT, maxWidth]);
}

async function initializeDetection() {
  const [dictionaryResponse, det] = await Promise.all([
    fetch(RECOGNITION_DICTIONARY),
    ort.InferenceSession.create(DETECTION_MODEL, {
      executionProviders: ["wasm"], graphOptimizationLevel: "all",
    }),
  ]);
  if (!dictionaryResponse.ok) throw new Error(`Dictionary HTTP ${dictionaryResponse.status}`);
  detectionSession = det;
  return dictionaryResponse.json();
}

async function initializeRecognition() {
  recognitionSession = await ort.InferenceSession.create(RECOGNITION_MODEL, {
    executionProviders: ["wasm"], graphOptimizationLevel: "all",
  });
}

async function detect(sourceCanvas) {
  const dimensions = detectionDimensions(sourceCanvas.width, sourceCanvas.height);
  const canvas = drawScaled(sourceCanvas, dimensions.width, dimensions.height);
  const output = await runSession(
    detectionSession,
    tensorFromCanvas(canvas, DETECTION_NORMALIZE),
  );
  canvas.width = 1;
  canvas.height = 1;
  const dims = output.dims;
  if (dims.length !== 3 && dims.length !== 4) {
    throw new Error(`Unexpected detection output: [${dims.join(",")}]`);
  }
  const height = dims.length === 4 ? dims[2] : dims[1];
  const width = dims.length === 4 ? dims[3] : dims[2];
  return decodeDetectionMap(
    output.data,
    width,
    height,
    sourceCanvas.width,
    sourceCanvas.height,
    { threshold: 0.3, boxThreshold: 0.6, unclipRatio: 1.5 },
  );
}

async function recognizeBoxes(refinedBoxes, dictionary) {
  const samples = refinedBoxes.map((box, index) => prepareRecognitionSample(
    box.crop, index,
  )).sort((a, b) => a.width - b.width);
  const decoded = [];
  for (let start = 0; start < samples.length; start += RECOGNITION_BATCH_SIZE) {
    const batch = samples.slice(start, start + RECOGNITION_BATCH_SIZE);
    const output = await runSession(recognitionSession, recognitionBatchTensor(batch));
    for (let index = 0; index < batch.length; index += 1) {
      decoded.push({ inputIndex: batch[index].inputIndex, ...decodeCtc(
        output.data, output.dims, dictionary, index,
      ) });
    }
    progress(60 + Math.round(30 * Math.min(samples.length, start + batch.length)
      / Math.max(1, samples.length)));
  }
  decoded.sort((a, b) => a.inputIndex - b.inputIndex);
  return decoded.map(({ inputIndex, ...result }) => ({
    ...result,
    poly: refinedBoxes[inputIndex].poly,
  })).filter((item) => item.text && item.score >= 0.0);
}

async function dispose() {
  const sessions = [detectionSession, recognitionSession];
  detectionSession = null;
  recognitionSession = null;
  await Promise.all(sessions.map((session) => session?.release()));
}

async function recognize(image, includeItems = false) {
  progress(5);
  const bitmap = await createImageBitmap(image);
  const sourceDimensions = resizeWithin(bitmap.width, bitmap.height, MAX_SOURCE_EDGE);
  const sourceCanvas = drawScaled(bitmap, sourceDimensions.width, sourceDimensions.height);
  bitmap.close();
  progress(10);
  const dictionary = await initializeDetection();
  progress(35);
  const boxes = await detect(sourceCanvas);
  const refinedBoxes = refineDetectedBoxes(sourceCanvas, boxes);
  await detectionSession.release();
  detectionSession = null;
  await initializeRecognition();
  progress(60);
  const items = (await recognizeBoxes(refinedBoxes, dictionary)).map((item, index) => ({
    ...item,
    id: `region-${String(index + 1).padStart(4, "0")}`,
  }));
  sourceCanvas.width = 1;
  sourceCanvas.height = 1;
  await dispose();
  const parsed = parseDocumentItems(items);
  self.postMessage({
    type: "result",
    rows: parsed.rows,
    uncertainty_codes: parsed.uncertainty_codes,
    region_count: items.length,
    ...(includeItems ? { items } : {}),
  });
}

self.onmessage = (event) => {
  if (event.data?.type !== "recognize") return;
  if (running) {
    self.postMessage({ type: "error", error: "OCR worker is already running" });
    return;
  }
  running = true;
  void recognize(event.data.image, event.data.include_items === true).catch(async (error) => {
    await dispose().catch(() => null);
    self.postMessage({
      type: "error",
      error: error instanceof Error ? error.message : "Direct ONNX OCR failed",
    });
  });
};
