export const RECOGNITION_EVAL_POLICY = Object.freeze({
  id: "severe-motion-downscale-jpeg-v1",
  difficulty: "hard",
  required_components: ["motion_blur", "downscale", "jpeg_compression"],
  minimum_motion_blur_radius: 3.5,
  maximum_downscale_factor: 0.65,
  maximum_jpeg_quality: 60,
});

export function recognitionOodTag(capture) {
  if (!capture || typeof capture !== "object" || Array.isArray(capture)) return null;
  if (capture.difficulty !== RECOGNITION_EVAL_POLICY.difficulty) return null;
  if (!Array.isArray(capture.augmentation_components)) return null;
  if (!RECOGNITION_EVAL_POLICY.required_components.every((name) => capture.augmentation_components.includes(name))) {
    return null;
  }
  if (!Number.isFinite(capture.motion_blur_radius)
    || capture.motion_blur_radius < RECOGNITION_EVAL_POLICY.minimum_motion_blur_radius) return null;
  if (!Number.isFinite(capture.downscale_factor)
    || capture.downscale_factor > RECOGNITION_EVAL_POLICY.maximum_downscale_factor) return null;
  if (!Number.isFinite(capture.jpeg_quality)
    || capture.jpeg_quality > RECOGNITION_EVAL_POLICY.maximum_jpeg_quality) return null;
  return "degradation-hard-ood";
}