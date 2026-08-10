FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS browser-ocr

WORKDIR /build

COPY browser_ocr/package.json browser_ocr/package-lock.json ./
COPY browser_ocr/THIRD_PARTY_NOTICES.md ./

RUN npm ci --ignore-scripts --no-audit --no-fund \
    && mkdir -p /out/core /out/lang /out/licenses \
    && cp node_modules/tesseract.js/dist/tesseract.min.js /out/ \
    && cp node_modules/tesseract.js/dist/worker.min.js /out/ \
    && cp node_modules/tesseract.js-core/tesseract-core*.wasm.js /out/core/ \
    && cp node_modules/@tesseract.js-data/kor/4.0.0_best_int/kor.traineddata.gz /out/lang/ \
    && cp node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz /out/lang/ \
    && cp node_modules/tesseract.js/LICENSE.md /out/licenses/tesseract.js-Apache-2.0.txt \
    && cp node_modules/tesseract.js-core/LICENSE /out/licenses/tesseract.js-core-Apache-2.0.txt \
    && cp THIRD_PARTY_NOTICES.md /out/licenses/

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

WORKDIR /app

COPY pyproject.toml ./
COPY medicine_dur ./medicine_dur
COPY medicine_app ./medicine_app
COPY medicine_catalog ./medicine_catalog
COPY --from=browser-ocr /out /opt/medicine-browser-ocr

RUN pip install --no-cache-dir .
