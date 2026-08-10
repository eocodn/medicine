# Browser OCR third-party notices

- `@paddleocr/paddleocr-js` 0.4.2 and PP-OCRv5 model assets: Apache License 2.0,
  <https://github.com/PaddlePaddle/PaddleOCR>
- ONNX Runtime Web 1.27.0: MIT License,
  <https://github.com/microsoft/onnxruntime>
- `@techstark/opencv-js` 4.10.0-release.1: Apache License 2.0,
  <https://github.com/TechStark/opencv-js>
- `clipper-lib` 6.4.2: Boost Software License 1.0,
  <https://sourceforge.net/projects/jsclipper/>
- `js-yaml` 4.3.1: MIT License,
  <https://github.com/nodeca/js-yaml>

The browser provider forces the ONNX Runtime WebAssembly CPU backend and loads all runtime
and model assets from the application origin. Package versions and integrity hashes are fixed
in `package-lock.json`; model archives are verified by SHA-256 during the Docker build.
