FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends fonts-noto-cjk=1:20240730+repack1-1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir Pillow==11.3.0

WORKDIR /workspace
