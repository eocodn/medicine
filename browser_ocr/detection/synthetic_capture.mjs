import { CAPTURE_PROFILES } from "./synthetic_catalog.mjs";

function rounded(value) {
  return Math.round(value * 1e9) / 1e9;
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

function destinationFor(profile, random, width, height) {
  if (profile === "flat_scan") return [[0, 0], [width, 0], [width, height], [0, height]];
  if (profile === "perspective_phone") {
    return [
      [55 + jitter(random, 22), 70 + jitter(random, 24)],
      [width - 45 + jitter(random, 20), 25 + jitter(random, 20)],
      [width - 25 + jitter(random, 18), height - 45 + jitter(random, 20)],
      [35 + jitter(random, 18), height - 15 + jitter(random, 18)],
    ];
  }
  if (profile === "cropped_clutter") {
    // Only the outer page boundary leaves the camera frame. Layout text is deliberately
    // inset far enough that every annotated quadrilateral remains fully observable.
    return [
      [-24 + jitter(random, 10), -18 + jitter(random, 10)],
      [width + 18 + jitter(random, 8), 18 + jitter(random, 12)],
      [width + 22 + jitter(random, 8), height + 14 + jitter(random, 10)],
      [-16 + jitter(random, 8), height - 8 + jitter(random, 12)],
    ];
  }
  const inset = profile === "glare_shadow" ? 32 : 18;
  return [
    [inset + jitter(random, 12), inset + jitter(random, 10)],
    [width - inset + jitter(random, 12), inset + jitter(random, 10)],
    [width - inset + jitter(random, 12), height - inset + jitter(random, 10)],
    [inset + jitter(random, 12), height - inset + jitter(random, 10)],
  ];
}

export function captureForIndex(index, random, width, height) {
  const profile = CAPTURE_PROFILES[index % CAPTURE_PROFILES.length];
  const source_corners = [[0, 0], [width, 0], [width, height], [0, height]];
  const destination_corners = destinationFor(profile, random, width, height).map(([x, y]) => [rounded(x), rounded(y)]);
  const homography = homographyFromQuads(source_corners, destination_corners);
  const projective = Math.abs(homography[6]) > 1e-10 || Math.abs(homography[7]) > 1e-10;

  const capture = {
    profile,
    geometry_model: projective ? "projective" : "homography_affine",
    source_corners,
    destination_corners,
    homography,
    defocus_radius: 0,
    motion_blur_radius: 0,
    motion_blur_angle: 0,
    contrast: 1,
    brightness: 1,
    jpeg_quality: 92,
    glare_opacity: 0,
    shadow_opacity: 0,
    camera_failure_modes: [],
    risk_tags: projective ? ["projective_geometry"] : [],
  };

  if (profile === "perspective_phone") {
    capture.jpeg_quality = 88;
    capture.shadow_opacity = 0.09;
    capture.camera_failure_modes.push("perspective");
  } else if (profile === "low_contrast_defocus") {
    capture.defocus_radius = rounded(0.75 + random() * 0.55);
    capture.contrast = rounded(0.72 + random() * 0.12);
    capture.brightness = rounded(0.96 + random() * 0.05);
    capture.jpeg_quality = 82;
    capture.camera_failure_modes.push("defocus", "low_contrast");
    capture.risk_tags.push("blur", "low_contrast");
  } else if (profile === "glare_shadow") {
    capture.glare_opacity = rounded(0.23 + random() * 0.16);
    capture.shadow_opacity = rounded(0.12 + random() * 0.08);
    capture.jpeg_quality = 86;
    capture.camera_failure_modes.push("specular_glare", "uneven_lighting");
    capture.risk_tags.push("glare", "uneven_lighting", "plastic_reflection");
  } else if (profile === "motion_jpeg") {
    capture.motion_blur_radius = rounded(3.5 + random() * 3.5);
    capture.motion_blur_angle = rounded(-12 + random() * 24);
    capture.jpeg_quality = Math.round(48 + random() * 16);
    capture.camera_failure_modes.push("motion_blur", "jpeg_compression");
    capture.risk_tags.push("motion_blur", "jpeg_artifacts", "blur");
  } else if (profile === "cropped_clutter") {
    capture.jpeg_quality = 78;
    capture.shadow_opacity = 0.08;
    capture.camera_failure_modes.push("partial_crop", "foreground_clutter");
    capture.risk_tags.push("partial_crop", "clutter");
  }
  capture.risk_tags = [...new Set(capture.risk_tags)];
  return capture;
}