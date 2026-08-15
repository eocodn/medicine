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

function overlayArgs(capture, appearance, width, height) {
  const args = [];
  if (appearance.background_profile === "pharmacy_counter") {
    args.push(
      "-fill", "rgba(255,255,255,0.16)", "-draw", `roundrectangle 20,55 ${Math.round(width * 0.24)},${Math.round(height * 0.18)} 24,24`,
      "-fill", "rgba(30,70,90,0.12)", "-draw", `rectangle ${Math.round(width * 0.78)},35 ${width - 10},${Math.round(height * 0.13)}`,
    );
  }
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
    // Foreground objects stay on document margins. Covering annotated medication text
    // would turn detector misses into ambiguous labels rather than useful stress cases.
    args.push(
      "-fill", "rgba(205,165,135,0.88)",
      "-draw", `ellipse ${width - 45},${height - 70} 145,230 0,360`,
      "-fill", "rgba(45,48,52,0.9)",
      "-draw", `roundrectangle 0,${Math.round(height * 0.18)} 42,${Math.round(height * 0.62)} 16,16`,
      "-fill", "rgba(70,85,105,0.84)",
      "-draw", `polygon ${width - 35},85 ${width},72 ${width},420 ${width - 18},430`,
    );
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
      "-size", `${width}x${height}`,
      `xc:${backgroundColor(appearance.background_profile)}`,
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
    if (capture.defocus_radius > 0) args.push("-blur", `0x${capture.defocus_radius}`);
    if (capture.motion_blur_radius > 0) {
      args.push("-motion-blur", `0x${capture.motion_blur_radius}+${capture.motion_blur_angle}`);
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