FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS browser-ocr

WORKDIR /build

COPY browser_ocr/package.json browser_ocr/package-lock.json ./
COPY browser_ocr/THIRD_PARTY_NOTICES.md ./
COPY browser_ocr/fetch_assets.mjs ./
COPY browser_ocr/prepare_models.mjs ./
COPY browser_ocr/src ./src

RUN npm ci --ignore-scripts --no-audit --no-fund

RUN mkdir -p /downloads \
    && node fetch_assets.mjs /downloads

RUN mkdir -p /tmp/detection /tmp/recognition /out/direct /out/models /out/ort /out/licenses \
    && tar -xf /downloads/PP-OCRv5_mobile_det_onnx_infer.tar -C /tmp/detection --strip-components=1 \
    && tar -xf /downloads/korean_PP-OCRv5_mobile_rec_onnx_infer.tar -C /tmp/recognition --strip-components=1 \
    && node prepare_models.mjs /tmp/detection /tmp/recognition /out/models \
    && node_modules/.bin/esbuild src/direct-ocr-worker.js \
        --bundle --format=iife --platform=browser \
        --outfile=/out/direct/ocr-worker.js \
    && cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs /out/ort/ \
    && cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm /out/ort/ \
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
