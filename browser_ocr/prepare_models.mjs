import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import yaml from "js-yaml";

const [detectionDirectory, recognitionDirectory, outputDirectory] = process.argv.slice(2);
if (!detectionDirectory || !recognitionDirectory || !outputDirectory) {
  throw new Error("usage: node prepare_models.mjs DETECTION_DIR RECOGNITION_DIR OUTPUT_DIR");
}

const recognitionConfig = yaml.load(await readFile(join(recognitionDirectory, "inference.yml"), "utf8"));
const dictionary = recognitionConfig?.PostProcess?.character_dict;
if (!Array.isArray(dictionary) || dictionary.length === 0
    || dictionary.some((value) => typeof value !== "string")) {
  throw new Error("recognition character dictionary is missing or malformed");
}

await mkdir(outputDirectory, { recursive: true });
await Promise.all([
  copyFile(join(detectionDirectory, "inference.onnx"), join(outputDirectory, "detection.onnx")),
  copyFile(join(recognitionDirectory, "inference.onnx"), join(outputDirectory, "korean-recognition.onnx")),
  writeFile(
    join(outputDirectory, "korean-recognition-dictionary.json"),
    `${JSON.stringify([...dictionary, " "])}\n`,
    { flag: "wx" },
  ),
]);
