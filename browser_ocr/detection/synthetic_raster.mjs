import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { rm, writeFile } from "node:fs/promises";

import { backgroundColor } from "./synthetic_appearance.mjs";

let cachedIdentity = null;

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${command} failed with exit ${code}: ${stderr.trim().slice(-2000)}`));
    });
  });
}

export async function rasterizerIdentity() {
  if (cachedIdentity) return cachedIdentity;
  const { stdout } = await run("convert", ["-version"]);
  const { stdout: svgDelegateStdout } = await run("rsvg-convert", ["--version"]);
  const versionLine = stdout.split(/\r?\n/).find((line) => line.trim())?.trim();
  const svgDelegate = svgDelegateStdout.trim();
  if (!versionLine || !versionLine.startsWith("Version: ImageMagick")) {
    throw new Error("unsupported rasterizer: ImageMagick convert is required");
  }
  if (!svgDelegate.startsWith("rsvg-convert")) throw new Error("unsupported SVG rasterizer delegate");
  const identityMaterial = `${stdout.trim()}\n${svgDelegate}`;
  cachedIdentity = {
    engine: "imagemagick-convert",
    version: versionLine,
    svg_delegate: svgDelegate,
    fingerprint: digest(identityMaterial),
  };
  return cachedIdentity;
}

function perspectiveSpec(capture) {
  return capture.source_corners.map(([sx, sy], index) => {
    const [dx, dy] = capture.destination_corners[index];
    return `${sx},${sy} ${dx},${dy}`;
  }).join(" ");
}

function sceneRandom(seed) {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

function backgroundArgs(appearance, width, height) {
  const profile = appearance.background_profile;
  const seed = appearance.texture_seed >>> 0;
  const args = ["-size", `${width}x${height}`, `xc:${backgroundColor(profile)}`];
  const noise = profile === "stone_speckle" ? 0.075 : profile === "wood_warm" ? 0.035 : 0.018;
  args.push("-seed", String(seed), "-attenuate", String(noise), "+noise", "Gaussian");

  if (profile === "wood_warm") {
    const random = sceneRandom(seed ^ 0x6a09e667);
    for (let row = 0; row < 18; row += 1) {
      const y = Math.round((row + 0.4) * height / 18 + (random() * 2 - 1) * 22);
      const bend1 = Math.round((random() * 2 - 1) * 26);
      const bend2 = Math.round((random() * 2 - 1) * 24);
      args.push(
        "-stroke", row % 3 === 0 ? "rgba(55,31,19,0.24)" : "rgba(72,43,27,0.15)",
        "-strokewidth", row % 4 === 0 ? "3" : "1.5",
        "-fill", "none",
        "-draw", `bezier 0,${y} ${Math.round(width * 0.34)},${y + bend1} ${Math.round(width * 0.68)},${y + bend2} ${width},${y + Math.round((random() * 2 - 1) * 15)}`,
      );
    }
    args.push(
      "-stroke", "none", "-fill", "rgba(255,244,220,0.08)",
      "-draw", `ellipse ${Math.round(width * 0.54)},${Math.round(height * 0.06)} ${Math.round(width * 0.45)},${Math.round(height * 0.13)} 0,360`,
    );
  } else if (profile === "stone_speckle") {
    const random = sceneRandom(seed ^ 0xbb67ae85);
    for (let dot = 0; dot < 95; dot += 1) {
      const x = Math.round(random() * width);
      const y = Math.round(random() * height);
      const radius = 1 + Math.round(random() * 3);
      const alpha = 0.12 + random() * 0.18;
      args.push(
        "-stroke", "none",
        "-fill", random() < 0.58 ? `rgba(46,45,43,${alpha})` : `rgba(225,222,214,${alpha * 0.75})`,
        "-draw", `circle ${x},${y} ${x + radius},${y}`,
      );
    }
  } else if (profile === "pharmacy_counter") {
    args.push(
      "-stroke", "rgba(92,121,118,0.10)", "-strokewidth", "2", "-fill", "none",
      "-draw", `line 0,${Math.round(height * 0.72)} ${width},${Math.round(height * 0.7)}`,
      "-stroke", "none", "-fill", "rgba(255,255,255,0.09)",
      "-draw", `ellipse ${Math.round(width * 0.18)},${Math.round(height * 0.08)} ${Math.round(width * 0.28)},${Math.round(height * 0.11)} 0,360`,
    );
  } else if (profile === "desk_dark") {
    args.push(
      "-stroke", "none", "-fill", "rgba(255,255,255,0.045)",
      "-draw", `ellipse ${Math.round(width * 0.56)},${Math.round(height * 0.12)} ${Math.round(width * 0.6)},${Math.round(height * 0.18)} 0,360`,
    );
  }
  return args;
}

export function foregroundClutterArgs(seed, width, height) {
  const skinPalettes = [
    ["#c99c7d", "#ddb69a", "#ead0ba"],
    ["#b98266", "#d09d7f", "#e3bea5"],
    ["#d2aa88", "#e2bea0", "#efd2bb"],
  ];
  const palette = skinPalettes[(seed >>> 0) % skinPalettes.length];
  const fingerX = width + 22;
  const fingerY = height - 82;
  const cardLeft = width - 38;
  const penTipY = Math.round(height * 0.39);
  return [
    // A partial receipt/card edge is a common camera-frame artifact. Keep it in
    // the outermost margin so annotated medication text remains unobstructed.
    "-fill", "rgba(25,28,31,0.18)", "-stroke", "none",
    "-draw", `polygon ${cardLeft - 5},54 ${width},47 ${width},378 ${cardLeft + 2},388`,
    "-fill", "rgba(242,239,228,0.94)", "-stroke", "rgba(130,126,117,0.48)", "-strokewidth", "1",
    "-draw", `polygon ${cardLeft},49 ${width},42 ${width},370 ${cardLeft + 6},380`,
    "-stroke", "rgba(105,105,100,0.34)", "-strokewidth", "1", "-fill", "none",
    "-draw", `line ${cardLeft + 8},105 ${width},101`,
    "-draw", `line ${cardLeft + 10},137 ${width},133`,
    "-draw", `line ${cardLeft + 12},169 ${width},165`,
    // A narrow pen entering from the left reads as an actual foreground object
    // rather than the previous abstract vertical bar.
    "-fill", "rgba(34,42,50,0.92)", "-stroke", "rgba(20,24,28,0.56)", "-strokewidth", "1",
    "-draw", `polygon 0,${penTipY - 180} 12,${penTipY - 186} 42,${penTipY - 20} 28,${penTipY - 14}`,
    "-fill", "rgba(185,190,194,0.92)", "-stroke", "none",
    "-draw", `polygon 28,${penTipY - 14} 42,${penTipY - 20} 38,${penTipY + 9}`,
    "-fill", "rgba(50,54,58,0.88)",
    "-draw", `polygon 38,${penTipY + 9} 42,${penTipY - 20} 47,${penTipY + 4}`,
    // Fingertip + nail/highlight: only the outer ~65 px enter the frame.
    "-fill", palette[0], "-stroke", "rgba(110,76,60,0.24)", "-strokewidth", "1",
    "-draw", `ellipse ${fingerX},${fingerY} 86,154 0,360`,
    "-fill", palette[1], "-stroke", "none",
    "-draw", `ellipse ${width - 19},${height - 112} 31,48 0,360`,
    "-fill", palette[2],
    "-draw", `ellipse ${width - 22},${height - 122} 19,31 0,360`,
    "-fill", "rgba(255,245,235,0.16)",
    "-draw", `ellipse ${width - 44},${height - 175} 24,62 0,360`,
  ];
}

function overlayArgs(capture, appearance, width, height) {
  const args = [];
  if (capture.shadow_opacity > 0) {
    const alpha = Math.round(capture.shadow_opacity * 255);
    args.push(
      "-fill", `rgba(20,20,20,${alpha / 255})`,
      "-draw", `polygon 15,${height - 95} ${Math.round(width * 0.45)},${height - 35} ${width - 5},${height - 120} ${width - 5},${height} 15,${height}`,
    );
  }
  if (capture.glare_opacity > 0) {
    args.push(
      "-fill", `rgba(255,255,255,${capture.glare_opacity})`,
      "-draw", `ellipse ${Math.round(width * 0.73)},${Math.round(height * 0.34)} 300,125 0,360`,
      "-stroke", `rgba(255,255,255,${Math.min(0.22, capture.glare_opacity)})`,
      "-strokewidth", "18", "-fill", "none",
      "-draw", `bezier ${Math.round(width * 0.18)},${Math.round(height * 0.15)} ${Math.round(width * 0.38)},${Math.round(height * 0.37)} ${Math.round(width * 0.63)},${Math.round(height * 0.58)} ${Math.round(width * 0.8)},${Math.round(height * 0.84)}`,
      "-stroke", "none",
    );
  }
  if (capture.profile === "cropped_clutter") {
    args.push(...foregroundClutterArgs(appearance.texture_seed, width, height));
  }
  return args;
}

export async function renderRasterJpeg({ sourceSvg, outputPath, capture, appearance, width, height }) {
  await rasterizerIdentity();
  const sourcePath = `${outputPath}.source-${process.pid}.svg`;
  const temporaryOutput = `${outputPath}.tmp-${process.pid}.jpg`;
  await writeFile(sourcePath, sourceSvg);
  try {
    const brightness = Math.round((capture.brightness - 1) * 100);
    const contrast = Math.round((capture.contrast - 1) * 100);
    const args = [
      ...backgroundArgs(appearance, width, height),
      "(", sourcePath,
      "-alpha", "set",
      "-background", "none",
      "-virtual-pixel", "transparent",
      "-distort", "Perspective", perspectiveSpec(capture),
      ")",
      "-gravity", "northwest",
      "-compose", "over",
      "-composite",
    ];
    args.push(...overlayArgs(capture, appearance, width, height));
    if (brightness !== 0 || contrast !== 0) args.push("-brightness-contrast", `${brightness}x${contrast}`);
    if (capture.red_gain !== 1) args.push("-channel", "R", "-evaluate", "multiply", String(capture.red_gain), "+channel");
    if (capture.blue_gain !== 1) args.push("-channel", "B", "-evaluate", "multiply", String(capture.blue_gain), "+channel");
    if (capture.defocus_radius > 0) args.push("-blur", `0x${capture.defocus_radius}`);
    if (capture.motion_blur_radius > 0) {
      args.push("-motion-blur", `0x${capture.motion_blur_radius}+${capture.motion_blur_angle}`);
    }
    if (capture.downscale_factor < 0.999) {
      const reducedWidth = Math.max(64, Math.round(width * capture.downscale_factor));
      const reducedHeight = Math.max(64, Math.round(height * capture.downscale_factor));
      args.push(
        "-filter", "Lanczos", "-resize", `${reducedWidth}x${reducedHeight}!`,
        "-filter", "Triangle", "-resize", `${width}x${height}!`,
      );
    }
    if (capture.sensor_noise > 0) {
      args.push(
        "-seed", String(capture.noise_seed >>> 0),
        "-attenuate", String(capture.sensor_noise),
        "+noise", "Gaussian",
      );
    }
    args.push(
      "-strip",
      "-interlace", "none",
      "-sampling-factor", "4:2:0",
      "-quality", String(capture.jpeg_quality),
      temporaryOutput,
    );
    await run("convert", args);
    return temporaryOutput;
  } catch (error) {
    await rm(temporaryOutput, { force: true });
    throw error;
  } finally {
    await rm(sourcePath, { force: true });
  }
}