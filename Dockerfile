FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

WORKDIR /app

RUN pip install --no-cache-dir cryptography==50.0.0
