"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  decodeCtc,
  decodeDetectionMap,
  resizeWithin,
  rgbaToChw,
  sortReadingOrder,
} = require("../src/direct-ocr-core.js");

test("resizes the longest image edge while preserving aspect ratio", () => {
  assert.deepEqual(resizeWithin(1448, 1086, 1280), { width: 1280, height: 960 });
  assert.deepEqual(resizeWithin(960, 640, 1280), { width: 960, height: 640 });
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
