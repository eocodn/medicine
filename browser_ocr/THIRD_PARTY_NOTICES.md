# Browser OCR third-party notices

- PP-OCRv5 model assets: Apache License 2.0,
  <https://github.com/PaddlePaddle/PaddleOCR>
- ONNX Runtime Web 1.27.0: MIT License,
  <https://github.com/microsoft/onnxruntime>
- `js-yaml` 4.3.1: MIT License,
  <https://github.com/nodeca/js-yaml>

The browser provider forces the ONNX Runtime WebAssembly CPU backend and loads all runtime
and model assets from the application origin. Package versions and integrity hashes are fixed
in `package-lock.json`; model archives are verified by SHA-256 during the Docker build.
