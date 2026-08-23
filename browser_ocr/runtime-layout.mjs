export const RUNTIME_FILES = Object.freeze([
  "direct/ocr-worker.js",
  "licenses/THIRD_PARTY_NOTICES.md",
  "licenses/js-yaml-MIT.txt",
  "models/detection.onnx",
  "models/korean-recognition-dictionary.json",
  "models/korean-recognition.onnx",
  "ort/ort-wasm-simd-threaded.mjs",
  "ort/ort-wasm-simd-threaded.wasm",
  "runtime-manifest.json",
]);

export const RUNTIME_PAYLOAD_FILES = Object.freeze(
  RUNTIME_FILES.filter((name) => name !== "runtime-manifest.json"),
);

export const PARSER_RUNTIME_FILES = Object.freeze([
  "models/parser-manifest.json",
  "models/parser.onnx",
]);

export function runtimePayloadFiles(includeParser = false) {
  return Object.freeze([
    ...RUNTIME_PAYLOAD_FILES,
    ...(includeParser ? PARSER_RUNTIME_FILES : []),
  ]);
}

export function runtimeFiles(includeParser = false) {
  return Object.freeze([
    ...runtimePayloadFiles(includeParser),
    "runtime-manifest.json",
  ]);
}
