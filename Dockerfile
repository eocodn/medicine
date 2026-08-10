FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS browser-ocr

WORKDIR /build

COPY browser_ocr/package.json browser_ocr/package-lock.json ./
COPY browser_ocr/THIRD_PARTY_NOTICES.md ./
COPY browser_ocr/fetch_assets.mjs ./

RUN npm ci --ignore-scripts --no-audit --no-fund

RUN mkdir -p /out/models \
    && node fetch_assets.mjs /out/models

RUN mkdir -p /out/paddle/assets /out/ort /out/licenses \
    && node_modules/.bin/esbuild node_modules/@paddleocr/paddleocr-js/dist/index.mjs \
        --bundle --format=esm --platform=browser --external:fs --external:path \
        --outfile=/out/paddle/index.mjs \
    && cp node_modules/@paddleocr/paddleocr-js/dist/assets/worker-entry-C9UNuyOJ.js /out/paddle/assets/ \
    && cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.mjs /out/ort/ \
    && cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.jsep.wasm /out/ort/ \
    && cp node_modules/@techstark/opencv-js/LICENSE /out/licenses/opencv-js-Apache-2.0.txt \
    && cp node_modules/js-yaml/LICENSE /out/licenses/js-yaml-MIT.txt \
    && cp THIRD_PARTY_NOTICES.md /out/licenses/

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

WORKDIR /app

COPY pyproject.toml ./
COPY medicine_dur ./medicine_dur
COPY medicine_app ./medicine_app
COPY medicine_catalog ./medicine_catalog
COPY --from=browser-ocr /out /opt/medicine-browser-ocr

RUN pip install --no-cache-dir .
