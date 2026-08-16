"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  decodeCtc,
  decodeDetectionMap,
  foregroundColumnInk,
  horizontalSubpolygon,
  recognitionTargetWidth,
  resizeWithin,
  rgbaToChw,
  splitHorizontalInkRanges,
  sortReadingOrder,
} = require("../src/direct-ocr-core.js");

test("resizes the longest image edge while preserving aspect ratio", () => {
  assert.deepEqual(resizeWithin(1448, 1086, 1280), { width: 1280, height: 960 });
  assert.deepEqual(resizeWithin(960, 640, 1280), { width: 960, height: 640 });
});

test("recognition width avoids oversized padding and caps pathological crops", () => {
  assert.equal(recognitionTargetWidth(40, 48), 320);
  assert.equal(recognitionTargetWidth(800, 48), 800);
  assert.equal(recognitionTargetWidth(5000, 48), 1280);
});

test("normalizes RGBA pixels into BGR channel-first tensors", () => {
  const rgba = new Uint8ClampedArray([255, 128, 0, 255]);
  const chw = rgbaToChw(rgba, 1, 1, [0.5, 0.5, 0.5], [0.5, 0.5, 0.5]);
  assert.deepEqual(Array.from(chw).map((value) => Number(value.toFixed(4))), [-1, 0.0039, 1]);
});

test("decodes repeated CTC classes and blanks", () => {
  const classes = 4;
  const steps = [0, 1, 1, 0, 2];
  const logits = new Float32Array(steps.length * classes).fill(0.01);
  steps.forEach((value, index) => { logits[index * classes + value] = 0.9; });
  const decoded = decodeCtc(logits, [1, steps.length, classes], ["가", "나", " "]);
  assert.equal(decoded.text, "가나");
  assert.ok(Math.abs(decoded.score - 0.9) < 1e-6);
});

test("turns one connected DB probability region into an expanded text box", () => {
  const width = 20;
  const height = 12;
  const probabilities = new Float32Array(width * height);
  for (let y = 3; y <= 7; y += 1) {
    for (let x = 4; x <= 14; x += 1) probabilities[y * width + x] = 0.92;
  }
  const boxes = decodeDetectionMap(probabilities, width, height, 200, 120, {
    threshold: 0.3, boxThreshold: 0.6, unclipRatio: 1.5,
  });
  assert.equal(boxes.length, 1);
  assert.ok(boxes[0].score > 0.9);
  assert.equal(boxes[0].poly.length, 4);
  assert.ok(boxes[0].poly[0][0] < 40);
  assert.ok(boxes[0].poly[2][0] > 140);
});

test("orders boxes by line then left-to-right", () => {
  const boxes = [
    { poly: [[80, 12], [90, 12], [90, 20], [80, 20]] },
    { poly: [[10, 10], [20, 10], [20, 20], [10, 20]] },
    { poly: [[5, 40], [15, 40], [15, 50], [5, 50]] },
  ];
  assert.deepEqual(sortReadingOrder(boxes).map((box) => box.poly[0][0]), [10, 80, 5]);
});

test("splits a detector crop only across a large internal blank gap", () => {
  const height = 40;
  const ink = Array(200).fill(12);
  for (let x = 90; x < 120; x += 1) ink[x] = 0;

  assert.deepEqual(splitHorizontalInkRanges(ink, height), [[0, 105], [105, 200]]);
});

test("trims a tiny adjacent fragment rather than emitting a medication field", () => {
  const height = 40;
  const ink = Array(180).fill(0);
  for (let x = 0; x < 8; x += 1) ink[x] = 10;
  for (let x = 35; x < 180; x += 1) ink[x] = 10;

  assert.deepEqual(splitHorizontalInkRanges(ink, height), [[21, 180]]);
});

test("maps a refined horizontal crop range back onto the source quad", () => {
  const polygon = [[10, 20], [210, 30], [205, 90], [5, 80]];

  assert.deepEqual(
    horizontalSubpolygon(polygon, 50, 150, 200),
    [[60, 22.5], [160, 27.5], [155, 87.5], [55, 82.5]],
  );
});

test("foreground projection keeps text strokes but removes isolated speckle", () => {
  const width = 20;
  const height = 10;
  const rgba = new Uint8ClampedArray(width * height * 4).fill(255);
  function black(x, y) {
    const offset = (y * width + x) * 4;
    rgba[offset] = 0; rgba[offset + 1] = 0; rgba[offset + 2] = 0; rgba[offset + 3] = 255;
  }
  for (let y = 2; y <= 7; y += 1) for (let x = 2; x <= 5; x += 1) black(x, y);
  black(10, 5);

  const ink = foregroundColumnInk({ data: rgba, width, height });
  assert.ok(ink.slice(2, 6).every((value) => value >= 4));
  assert.equal(ink[10], 0);
});
