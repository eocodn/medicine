FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

RUN apt-get update \
    && apt-get install -y --no-install-recommends imagemagick librsvg2-bin fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace