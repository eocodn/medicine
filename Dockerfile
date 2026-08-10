FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
COPY medicine_dur ./medicine_dur
COPY medicine_app ./medicine_app
COPY medicine_catalog ./medicine_catalog

RUN pip install --no-cache-dir .
