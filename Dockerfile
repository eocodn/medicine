FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
COPY medicine_dur ./medicine_dur

RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "medicine_dur.cli"]
