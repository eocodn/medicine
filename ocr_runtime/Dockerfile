FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS deps
WORKDIR /opt/ocr-runtime
COPY ocr_runtime/package.json ocr_runtime/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM deps AS build
RUN --mount=type=bind,source=ocr_runtime,target=/source/ocr_runtime,ro \
    rm -rf /tmp/ocr-runtime /downloads /out \
    && mkdir -p /tmp/ocr-runtime /downloads \
    && cp -a /source/ocr_runtime/. /tmp/ocr-runtime/ \
    && cp -a /opt/ocr-runtime/node_modules /tmp/ocr-runtime/node_modules \
    && node /tmp/ocr-runtime/fetch_assets.mjs /downloads \
    && node /tmp/ocr-runtime/export_runtime.mjs /downloads /out

FROM scratch AS runtime
COPY --from=build /out /ocr-assets/
