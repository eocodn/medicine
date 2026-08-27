from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .release_r2_object_io import _download_to_file


LATEST_CACHE_CONTROL = "no-store"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_body_bytes(body) -> bytes:
    return body.read()


def configure_conditional_put_headers(client) -> None:
    events = client.meta.events

    def process_custom_arguments(params, context, **kwargs):
        custom_headers = params.pop("custom_headers", None)
        if custom_headers:
            context["custom_headers"] = custom_headers

    def add_custom_headers(params, context, **kwargs):
        custom_headers = context.get("custom_headers")
        if custom_headers:
            params["headers"].update(custom_headers)

    events.register("before-parameter-build.s3.PutObject", process_custom_arguments)
    events.register("before-call.s3.PutObject", add_custom_headers)


def client_from_env():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for R2 publication") from exc
    required = (
        "R2_ENDPOINT",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing R2 environment: {', '.join(missing)}")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    configure_conditional_put_headers(client)
    return client


def download_object_from_env(key: str, output_path: str | Path) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    if not key.strip():
        raise ValueError("R2 object key is required")
    output = Path(output_path)
    downloaded = _download_to_file(client_from_env(), bucket, key, output)
    return {"bucket": bucket, "key": key, "output_path": str(output), **downloaded}


__all__ = [
    "LATEST_CACHE_CONTROL",
    "client_from_env",
    "configure_conditional_put_headers",
    "download_object_from_env",
    "read_body_bytes",
    "sha256_bytes",
]