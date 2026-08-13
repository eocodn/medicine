FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS model-build

WORKDIR /pipeline/browser_ocr

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

FROM scratch AS runtime
COPY --from=model-build /out /ocr-assets/

FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS eval

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /pipeline
COPY --from=model-build /out /ocr-assets
COPY browser_ocr/eval ./browser_ocr/eval

ENTRYPOINT ["node", "/pipeline/browser_ocr/eval/run_eval.mjs", "--runtime-dir", "/ocr-assets", "--corpus", "/pipeline/browser_ocr/eval/corpus/manifest.json", "--json"]
