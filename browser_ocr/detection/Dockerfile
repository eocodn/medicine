FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

RUN apt-get update \
    && apt-get install -y --no-install-recommends imagemagick librsvg2-bin fonts-noto-cjk ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY browser_ocr/detection/requirements-benchmark.txt /tmp/requirements-benchmark.txt
RUN python3 -m venv /opt/detection-venv \
    && /opt/detection-venv/bin/pip install --no-cache-dir -r /tmp/requirements-benchmark.txt \
    && rm /tmp/requirements-benchmark.txt

ENV PATH="/opt/detection-venv/bin:${PATH}"

WORKDIR /workspace