import {
  AUGMENTATION_DIFFICULTIES,
  CAPTURE_PROFILES,
  LAYOUT_FAMILIES,
} from "./synthetic_catalog.mjs";

function rounded(value) {
  return Math.round(value * 1e9) / 1e9;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function between(random, minimum, maximum) {
  return minimum + random() * (maximum - minimum);
}

function solveLinear(matrix, vector) {
  const n = vector.length;
  const augmented = matrix.map((row, index) => [...row, vector[index]]);
  for (let pivot = 0; pivot < n; pivot += 1) {
    let best = pivot;
    for (let row = pivot + 1; row < n; row += 1) {
      if (Math.abs(augmented[row][pivot]) > Math.abs(augmented[best][pivot])) best = row;
    }
    if (Math.abs(augmented[best][pivot]) < 1e-12) throw new Error("singular homography control points");
    [augmented[pivot], augmented[best]] = [augmented[best], augmented[pivot]];
    const divisor = augmented[pivot][pivot];
    for (let column = pivot; column <= n; column += 1) augmented[pivot][column] /= divisor;
    for (let row = 0; row < n; row += 1) {
      if (row === pivot) continue;
      const factor = augmented[row][pivot];
      for (let column = pivot; column <= n; column += 1) {
        augmented[row][column] -= factor * augmented[pivot][column];
      }
    }
  }
  return augmented.map((row) => row[n]);
}

export function homographyFromQuads(source, destination) {
  if (source.length !== 4 || destination.length !== 4) throw new Error("homography requires four source and destination points");
  const matrix = [];
  const vector = [];
  for (let index = 0; index < 4; index += 1) {
    const [x, y] = source[index];
    const [u, v] = destination[index];
    matrix.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    vector.push(u);
    matrix.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    vector.push(v);
  }
  const coefficients = solveLinear(matrix, vector);
  return [...coefficients, 1];
}

export function transformPoint(homography, [x, y]) {
  const [h0, h1, h2, h3, h4, h5, h6, h7, h8] = homography;
  const denominator = h6 * x + h7 * y + h8;
  if (Math.abs(denominator) < 1e-12) throw new Error("homography maps point to infinity");
  return [
    rounded((h0 * x + h1 * y + h2) / denominator),
    rounded((h3 * x + h4 * y + h5) / denominator),
  ];
}

export function transformPolygon(homography, polygon) {
  return polygon.map((point) => transformPoint(homography, point));
}

function jitter(random, magnitude) {
  return (random() * 2 - 1) * magnitude;
}

export function augmentationDifficultyForSample(sampleIndex, profileIndex) {
  const layoutIndex = sampleIndex % LAYOUT_FAMILIES.length;
  return AUGMENTATION_DIFFICULTIES[(layoutIndex + profileIndex) % AUGMENTATION_DIFFICULTIES.length];
}

function destinationFor({ perspective, partialCrop, difficulty, random, width, height }) {
  const severity = difficulty === "hard" ? 1 : difficulty === "medium" ? 0.68 : 0.35;
  if (partialCrop) {
    const overflow = 12 + 26 * severity;
    const skew = perspective ? 24 * severity : 8 * severity;
    return [
      [-overflow + jitter(random, 8), -overflow * 0.55 + jitter(random, 7)],
      [width + overflow * 0.7 + jitter(random, 7), 12 + jitter(random, skew)],
      [width + overflow + jitter(random, 8), height + overflow * 0.6 + jitter(random, skew)],
      [-overflow * 0.65 + jitter(random, 7), height - 4 + jitter(random, skew)],
    ];
  }
  if (perspective) {
    const inset = 18 + 34 * severity;
    const skew = 12 + 22 * severity;
    return [
      [inset + jitter(random, skew), inset * 1.25 + jitter(random, skew)],
      [width - inset + jitter(random, skew), inset * 0.55 + jitter(random, skew)],
      [width - inset * 0.55 + jitter(random, skew), height - inset + jitter(random, skew)],
      [inset * 0.6 + jitter(random, skew), height - inset * 0.45 + jitter(random, skew)],
    ];
  }
  if (difficulty === "clean") return [[0, 0], [width, 0], [width, height], [0, height]];
  const inset = difficulty === "hard" ? 18 : 10;
  return [
    [inset + jitter(random, 6), inset + jitter(random, 5)],
    [width - inset + jitter(random, 6), inset + jitter(random, 5)],
    [width - inset + jitter(random, 6), height - inset + jitter(random, 5)],
    [inset + jitter(random, 6), height - inset + jitter(random, 5)],
  ];
}

function addComponent(capture, component, riskTag = null) {
  capture.augmentation_components.push(component);
  capture.camera_failure_modes.push(component);
  if (riskTag) capture.risk_tags.push(riskTag);
}

function applyAnchorProfile(capture, profile, difficulty, random) {
  const clean = difficulty === "clean";
  const hard = difficulty === "hard";
  if (profile === "perspective_phone") {
    addComponent(capture, "perspective", "projective_geometry");
  } else if (profile === "low_contrast_defocus") {
    capture.defocus_radius = rounded(between(random, clean ? 0.3 : hard ? 1.1 : 0.65, clean ? 0.7 : hard ? 2 : 1.25));
    capture.contrast = rounded(between(random, clean ? 0.88 : hard ? 0.62 : 0.74, clean ? 0.97 : hard ? 0.8 : 0.9));
    addComponent(capture, "defocus", "blur");
    addComponent(capture, "contrast_exposure", "low_contrast");
  } else if (profile === "glare_shadow") {
    capture.glare_opacity = rounded(between(random, clean ? 0.08 : hard ? 0.22 : 0.14, clean ? 0.16 : hard ? 0.38 : 0.28));
    capture.shadow_opacity = rounded(between(random, clean ? 0.05 : hard ? 0.14 : 0.09, clean ? 0.1 : hard ? 0.25 : 0.17));
    addComponent(capture, "glare", "glare");
    addComponent(capture, "shadow", "uneven_lighting");
  } else if (profile === "motion_jpeg") {
    capture.motion_blur_radius = rounded(between(random, clean ? 1.1 : hard ? 4 : 2.3, clean ? 2.4 : hard ? 7 : 4.5));
    capture.motion_blur_angle = rounded(between(random, -16, 16));
    capture.jpeg_quality = Math.round(between(random, clean ? 76 : hard ? 42 : 58, clean ? 88 : hard ? 62 : 76));
    addComponent(capture, "motion_blur", "motion_blur");
    capture.risk_tags.push("blur");
    addComponent(capture, "jpeg_compression", "jpeg_artifacts");
  } else if (profile === "cropped_clutter") {
    addComponent(capture, "partial_crop", "partial_crop");
    addComponent(capture, "foreground_clutter", "clutter");
  }
}

function applySharedDifficulty(capture, difficulty, random, sampleIndex) {
  const medium = difficulty === "medium";
  const hard = difficulty === "hard";
  if (medium || hard) {
    if (!capture.augmentation_components.includes("jpeg_compression")) {
      capture.jpeg_quality = Math.round(between(random, hard ? 48 : 72, hard ? 70 : 88));
      addComponent(capture, "jpeg_compression", "jpeg_artifacts");
    }
    if (!capture.augmentation_components.includes("contrast_exposure")) {
      capture.contrast = rounded(between(random, hard ? 0.72 : 0.86, hard ? 1.08 : 1.04));
      capture.brightness = rounded(between(random, hard ? 0.82 : 0.93, hard ? 1.12 : 1.07));
      addComponent(capture, "contrast_exposure", capture.contrast < 0.86 ? "low_contrast" : null);
    }
    capture.red_gain = rounded(between(random, hard ? 0.86 : 0.94, hard ? 1.14 : 1.06));
    capture.blue_gain = rounded(between(random, hard ? 0.86 : 0.94, hard ? 1.14 : 1.06));
    addComponent(capture, "white_balance", "white_balance");
  }

  if (hard) {
    capture.downscale_factor = rounded(between(random, 0.48, 0.72));
    capture.sensor_noise = rounded(between(random, 0.035, 0.13));
    capture.noise_seed = ((sampleIndex + 1) * 2654435761) >>> 0;
    addComponent(capture, "downscale", "downscale");
    addComponent(capture, "sensor_noise", "sensor_noise");
  } else if (medium) {
    if (random() < 0.48) {
      capture.downscale_factor = rounded(between(random, 0.72, 0.9));
      addComponent(capture, "downscale", "downscale");
    }
    if (random() < 0.38) {
      capture.sensor_noise = rounded(between(random, 0.015, 0.055));
      capture.noise_seed = ((sampleIndex + 1) * 2246822519) >>> 0;
      addComponent(capture, "sensor_noise", "sensor_noise");
    }
  }

  if ((medium || hard) && capture.motion_blur_radius === 0 && random() < (hard ? 0.55 : 0.18)) {
    capture.motion_blur_radius = rounded(between(random, hard ? 1.5 : 0.7, hard ? 4.2 : 2.2));
    capture.motion_blur_angle = rounded(between(random, -18, 18));
    addComponent(capture, "motion_blur", "motion_blur");
    capture.risk_tags.push("blur");
  }
  if ((medium || hard) && capture.defocus_radius === 0 && random() < (hard ? 0.42 : 0.16)) {
    capture.defocus_radius = rounded(between(random, hard ? 0.55 : 0.25, hard ? 1.35 : 0.7));
    addComponent(capture, "defocus", "blur");
  }
  if ((medium || hard) && capture.glare_opacity === 0 && random() < (hard ? 0.4 : 0.14)) {
    capture.glare_opacity = rounded(between(random, 0.08, hard ? 0.28 : 0.18));
    addComponent(capture, "glare", "glare");
  }
  if ((medium || hard) && capture.shadow_opacity === 0 && random() < (hard ? 0.5 : 0.2)) {
    capture.shadow_opacity = rounded(between(random, 0.05, hard ? 0.2 : 0.12));
    addComponent(capture, "shadow", "uneven_lighting");
  }
}

export function captureForSample(sampleIndex, profileIndex, random, width, height) {
  const profile = CAPTURE_PROFILES[profileIndex % CAPTURE_PROFILES.length];
  const difficulty = augmentationDifficultyForSample(sampleIndex, profileIndex);
  const capture = {
    profile,
    difficulty,
    geometry_model: "homography_affine",
    source_corners: [[0, 0], [width, 0], [width, height], [0, height]],
    destination_corners: [],
    homography: [],
    defocus_radius: 0,
    motion_blur_radius: 0,
    motion_blur_angle: 0,
    contrast: 1,
    brightness: 1,
    jpeg_quality: difficulty === "clean" ? Math.round(between(random, 90, 96)) : 90,
    glare_opacity: 0,
    shadow_opacity: 0,
    downscale_factor: 1,
    sensor_noise: 0,
    noise_seed: 0,
    red_gain: 1,
    blue_gain: 1,
    augmentation_components: [],
    camera_failure_modes: [],
    risk_tags: [],
  };

  applyAnchorProfile(capture, profile, difficulty, random);
  applySharedDifficulty(capture, difficulty, random, sampleIndex);

  if (capture.jpeg_quality < 90 && !capture.augmentation_components.includes("jpeg_compression")) {
    addComponent(capture, "jpeg_compression", "jpeg_artifacts");
  }

  let perspective = capture.augmentation_components.includes("perspective");
  let partialCrop = capture.augmentation_components.includes("partial_crop");
  if (!perspective && difficulty !== "clean" && random() < (difficulty === "hard" ? 0.52 : 0.2)) {
    perspective = true;
    addComponent(capture, "perspective", "projective_geometry");
  }
  if (!partialCrop && difficulty === "hard" && random() < 0.16) {
    partialCrop = true;
    addComponent(capture, "partial_crop", "partial_crop");
  }

  capture.destination_corners = destinationFor({ perspective, partialCrop, difficulty, random, width, height })
    .map(([x, y]) => [rounded(x), rounded(y)]);
  capture.homography = homographyFromQuads(capture.source_corners, capture.destination_corners);
  const projective = Math.abs(capture.homography[6]) > 1e-10 || Math.abs(capture.homography[7]) > 1e-10;
  capture.geometry_model = projective ? "projective" : "homography_affine";
  if (projective && !capture.risk_tags.includes("projective_geometry")) capture.risk_tags.push("projective_geometry");

  capture.jpeg_quality = Math.round(clamp(capture.jpeg_quality, 42, 96));
  capture.downscale_factor = rounded(clamp(capture.downscale_factor, 0.45, 1));
  capture.sensor_noise = rounded(clamp(capture.sensor_noise, 0, 0.35));
  capture.red_gain = rounded(clamp(capture.red_gain, 0.82, 1.18));
  capture.blue_gain = rounded(clamp(capture.blue_gain, 0.82, 1.18));
  capture.augmentation_components = [...new Set(capture.augmentation_components)];
  capture.camera_failure_modes = [...new Set(capture.camera_failure_modes)];
  capture.risk_tags = [...new Set(capture.risk_tags)];
  return capture;
}
