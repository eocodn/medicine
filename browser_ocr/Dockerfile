FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS deps

WORKDIR /opt/browser-ocr
COPY browser_ocr/package.json browser_ocr/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM deps AS model-build

RUN --mount=type=bind,source=browser_ocr,target=/source/browser_ocr,ro \
    rm -rf /tmp/browser-ocr \
    && mkdir -p /tmp/browser-ocr \
    && cp -a /source/browser_ocr/. /tmp/browser-ocr/ \
    && cp -a /opt/browser-ocr/node_modules /tmp/browser-ocr/node_modules \
    && mkdir -p /downloads \
    && node /tmp/browser-ocr/fetch_assets.mjs /downloads \
    && node /tmp/browser-ocr/export_runtime.mjs /downloads /out \
    && rm -rf /tmp/browser-ocr /downloads

FROM scratch AS runtime
COPY --from=model-build /out /ocr-assets/

FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS eval

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/browser-ocr/node_modules /workspace/browser_ocr/node_modules
WORKDIR /workspace
ENTRYPOINT ["sh", "/workspace/browser_ocr/eval/run_compose_eval.sh"]
