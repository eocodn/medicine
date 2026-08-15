import assert from "node:assert/strict";
import test from "node:test";

import {
  AUGMENTATION_DIFFICULTIES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
  REQUIRED_AUGMENTATION_COMPONENTS,
} from "../../detection/synthetic_catalog.mjs";
import { captureForSample } from "../../detection/synthetic_capture.mjs";
import { buildLayout, DOCUMENT_HEIGHT, DOCUMENT_WIDTH } from "../../detection/synthetic_layouts.mjs";

function seeded(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

test("v4 capture augmentation composes multiple bounded effects across difficulty buckets", () => {
  const captures = [];
  for (let index = 0; index < 36; index += 1) {
    const profileIndex = Math.floor(index / LAYOUT_FAMILIES.length) % CAPTURE_PROFILES.length;
    captures.push(captureForSample(index, profileIndex, seeded(1000 + index), DOCUMENT_WIDTH, DOCUMENT_HEIGHT));
  }

  assert.deepEqual(new Set(captures.map((capture) => capture.difficulty)), new Set(AUGMENTATION_DIFFICULTIES));
  const components = new Set(captures.flatMap((capture) => capture.augmentation_components));
  for (const component of REQUIRED_AUGMENTATION_COMPONENTS) assert.ok(components.has(component), component);

  for (const capture of captures) {
    assert.ok(capture.downscale_factor >= 0.45 && capture.downscale_factor <= 1);
    assert.ok(capture.sensor_noise >= 0 && capture.sensor_noise <= 0.35);
    assert.ok(capture.red_gain >= 0.82 && capture.red_gain <= 1.18);
    assert.ok(capture.blue_gain >= 0.82 && capture.blue_gain <= 1.18);
    assert.ok(capture.jpeg_quality >= 42 && capture.jpeg_quality <= 96);
    assert.equal(capture.augmentation_components.length, new Set(capture.augmentation_components).size);
  }

  assert.ok(captures.filter((capture) => capture.difficulty === "medium").every((capture) => capture.augmentation_components.length >= 2));
  assert.ok(captures.filter((capture) => capture.difficulty === "hard").every((capture) => capture.augmentation_components.length >= 4));
  assert.ok(captures.some((capture) => (
    capture.difficulty === "hard"
    && capture.motion_blur_radius > 0
    && capture.downscale_factor < 1
    && capture.sensor_noise > 0
  )));
});

test("v4 layout generation varies medication density and typography within each layout family", () => {
  for (let familyIndex = 0; familyIndex < LAYOUT_FAMILIES.length; familyIndex += 1) {
    const layouts = Array.from({ length: 6 }, (_, variant) => {
      const index = familyIndex + variant * LAYOUT_FAMILIES.length;
      return buildLayout(index, seeded(5000 + index));
    });
    const productCounts = new Set(layouts.map((layout) => (
      layout.regions.filter((region) => region.semantic_role === "product").length
    )));
    const medicationFonts = new Set(layouts.flatMap((layout) => (
      layout.regions.filter((region) => region.region_class === "medication").map((region) => region.font_size_px)
    )));
    assert.ok(productCounts.size > 1 || medicationFonts.size > 1, LAYOUT_FAMILIES[familyIndex]);
  }
});
