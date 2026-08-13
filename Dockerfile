FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS browser-ocr

WORKDIR /build

COPY browser_ocr/package.json browser_ocr/package-lock.json ./
COPY browser_ocr/THIRD_PARTY_NOTICES.md ./
COPY browser_ocr/model-manifest.json ./
COPY browser_ocr/runtime-layout.mjs ./
COPY browser_ocr/fetch_assets.mjs ./
COPY browser_ocr/export_runtime.mjs ./
COPY browser_ocr/src ./src

RUN npm ci --ignore-scripts --no-audit --no-fund
RUN mkdir -p /downloads && node fetch_assets.mjs /downloads
RUN node export_runtime.mjs /downloads /out

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

WORKDIR /app

COPY pyproject.toml ./
COPY medicine_app ./medicine_app
COPY medicine_canonical ./medicine_canonical
COPY --from=browser-ocr /out /opt/medicine-browser-ocr

RUN pip install --no-cache-dir .
