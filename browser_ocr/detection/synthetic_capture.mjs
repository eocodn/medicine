import { CAPTURE_PROFILES } from "./synthetic_catalog.mjs";

function rounded(value) {
  return Math.round(value * 1000) / 1000;
}

function centeredAffine({ width, height, scale = 1, angleDegrees = 0, shearX = 0, shearY = 0, translateX = 0, translateY = 0 }) {
  const angle = angleDegrees * Math.PI / 180;
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const a = scale * (cosine - sine * shearY);
  const c = scale * (cosine * shearX - sine);
  const b = scale * (sine + cosine * shearY);
  const d = scale * (sine * shearX + cosine);
  const cx = width / 2;
  const cy = height / 2;
  const e = cx + translateX - a * cx - c * cy;
  const f = cy + translateY - b * cx - d * cy;
  return [a, b, c, d, e, f].map(rounded);
}

export function transformPoint(matrix, [x, y]) {
  const [a, b, c, d, e, f] = matrix;
  return [rounded(a * x + c * y + e), rounded(b * x + d * y + f)];
}

export function transformPolygon(matrix, polygon) {
  return polygon.map((point) => transformPoint(matrix, point));
}

export function captureForIndex(index, random, width, height) {
  const profile = CAPTURE_PROFILES[index % CAPTURE_PROFILES.length];
  let capture;
  if (profile === "flat_clean") {
    capture = {
      profile,
      scale: 1,
      angle_degrees: 0,
      shear_x: 0,
      shear_y: 0,
      blur_px: 0,
      contrast: 1,
      brightness: 1,
      glare_opacity: 0,
      shadow_opacity: 0,
      risk_tags: [],
    };
  } else if (profile === "oblique_photo") {
    const sign = random() < 0.5 ? -1 : 1;
    capture = {
      profile,
      scale: 0.92,
      angle_degrees: rounded(sign * (1.2 + random() * 1.1)),
      shear_x: rounded(sign * (0.025 + random() * 0.018)),
      shear_y: rounded(-sign * random() * 0.012),
      blur_px: 0.08,
      contrast: 0.96,
      brightness: 0.99,
      glare_opacity: 0,
      shadow_opacity: 0.08,
      risk_tags: ["oblique_geometry", "rotation"],
    };
  } else if (profile === "low_contrast_blur") {
    const sign = random() < 0.5 ? -1 : 1;
    capture = {
      profile,
      scale: 0.95,
      angle_degrees: rounded(sign * random() * 0.8),
      shear_x: 0,
      shear_y: 0,
      blur_px: rounded(0.38 + random() * 0.32),
      contrast: rounded(0.74 + random() * 0.1),
      brightness: rounded(0.94 + random() * 0.04),
      glare_opacity: 0,
      shadow_opacity: 0.04,
      risk_tags: ["blur", "low_contrast"],
    };
  } else {
    const sign = random() < 0.5 ? -1 : 1;
    capture = {
      profile,
      scale: 0.94,
      angle_degrees: rounded(sign * (0.3 + random() * 0.7)),
      shear_x: rounded(sign * random() * 0.012),
      shear_y: 0,
      blur_px: 0.12,
      contrast: 0.9,
      brightness: 0.98,
      glare_opacity: rounded(0.22 + random() * 0.16),
      shadow_opacity: 0.14,
      risk_tags: ["glare", "uneven_lighting", "plastic_reflection"],
    };
  }
  capture.matrix = centeredAffine({
    width,
    height,
    scale: capture.scale,
    angleDegrees: capture.angle_degrees,
    shearX: capture.shear_x,
    shearY: capture.shear_y,
  });
  return capture;
}

export function renderCaptureDefinitions(capture) {
  const blur = capture.blur_px > 0 ? `    <feGaussianBlur stdDeviation="${capture.blur_px}"/>\n` : "";
  const intercept = rounded((capture.brightness - capture.contrast) / 2);
  return `<defs>
  <filter id="capture-filter" x="-10%" y="-10%" width="120%" height="120%">
${blur}    <feComponentTransfer>
      <feFuncR type="linear" slope="${capture.contrast}" intercept="${intercept}"/>
      <feFuncG type="linear" slope="${capture.contrast}" intercept="${intercept}"/>
      <feFuncB type="linear" slope="${capture.contrast}" intercept="${intercept}"/>
    </feComponentTransfer>
  </filter>
  <radialGradient id="capture-glare" cx="50%" cy="50%" r="50%">
    <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95"/>
    <stop offset="55%" stop-color="#ffffff" stop-opacity="0.45"/>
    <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
  </radialGradient>
</defs>`;
}

export function renderCaptureOverlays(capture, width, height, random) {
  const parts = [];
  if (capture.shadow_opacity > 0) {
    parts.push(`<path d="M60 ${height - 150} Q${width / 2} ${height - 85} ${width - 70} ${height - 165}" stroke="#111" stroke-width="70" opacity="${capture.shadow_opacity}" fill="none"/>`);
  }
  if (capture.glare_opacity > 0) {
    const cx = Math.round(width * (0.58 + random() * 0.18));
    const cy = Math.round(height * (0.28 + random() * 0.28));
    parts.push(`<ellipse cx="${cx}" cy="${cy}" rx="360" ry="170" transform="rotate(-22 ${cx} ${cy})" fill="url(#capture-glare)" opacity="${capture.glare_opacity}"/>`);
    parts.push(`<path d="M${Math.round(width * 0.18)} ${Math.round(height * 0.18)} Q${Math.round(width * 0.48)} ${Math.round(height * 0.46)} ${Math.round(width * 0.78)} ${Math.round(height * 0.82)}" stroke="#ffffff" stroke-width="18" opacity="${Math.min(0.28, capture.glare_opacity)}" fill="none"/>`);
  }
  return parts.join("\n");
}