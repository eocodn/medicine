import assert from "node:assert/strict";
import test from "node:test";

import { RECOGNITION_EVAL_POLICY, recognitionOodTag } from "../evaluation_policy.mjs";

function capture(overrides = {}) {
  return {
    difficulty: "hard",
    motion_blur_radius: 3.5,
    downscale_factor: 0.65,
    jpeg_quality: 60,
    augmentation_components: ["motion_blur", "downscale", "jpeg_compression"],
    ...overrides,
  };
}

test("fixed recognition OOD policy is explicit and boundary-inclusive", () => {
  assert.equal(RECOGNITION_EVAL_POLICY.id, "severe-motion-downscale-jpeg-v1");
  assert.equal(recognitionOodTag(capture()), "degradation-hard-ood");
  assert.equal(recognitionOodTag(capture({ motion_blur_radius: 3.49 })), null);
  assert.equal(recognitionOodTag(capture({ downscale_factor: 0.651 })), null);
  assert.equal(recognitionOodTag(capture({ jpeg_quality: 61 })), null);
  assert.equal(recognitionOodTag(capture({ difficulty: "medium" })), null);
  assert.equal(
    recognitionOodTag(capture({ augmentation_components: ["motion_blur", "jpeg_compression"] })),
    null,
  );
});