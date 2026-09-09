from __future__ import annotations

import os
import re

from .release_r2_runtime import client_from_env
from .release_window import ROOT_KEY


PUBLIC_REFERENCE_PREFIX = "reference/v2/"
_VERSION_NAMESPACE = re.compile(r"reference/v[1-9][0-9]*/\Z")


def _list_all_keys(client, bucket: str, *, prefix: str = "") -> list[str]:
    keys: list[str] = []
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, str] = {"Bucket": bucket}
        if prefix:
            kwargs["Prefix"] = prefix
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = item.get("Key")
            if isinstance(key, str):
                keys.append(key)
        if not response.get("IsTruncated"):
            return sorted(keys)
        continuation_token = response.get("NextContinuationToken")
        if not isinstance(continuation_token, str) or not continuation_token:
            raise RuntimeError("R2 bucket listing is truncated without continuation token")


def audit_public_bucket(client, bucket: str) -> dict:
    if not bucket.strip():
        raise ValueError("R2 bucket is required")
    keys = _list_all_keys(client, bucket)
    unexpected = [key for key in keys if not key.startswith(PUBLIC_REFERENCE_PREFIX)]
    if unexpected:
        preview = ", ".join(unexpected[:5])
        raise RuntimeError(
            "R2 bucket contains objects outside public reference namespaces "
            f"and cannot be exposed: {preview}"
        )
    if ROOT_KEY not in keys:
        raise RuntimeError(f"R2 bucket does not contain required {ROOT_KEY}")
    return {
        "status": "safe_to_expose",
        "bucket": bucket,
        "object_count": len(keys),
        "unexpected_keys": [],
        "latest_key": ROOT_KEY,
    }


def audit_public_bucket_from_env() -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    return audit_public_bucket(client_from_env(), bucket)


def retire_reference_namespace(client, bucket: str, namespace: str) -> dict:
    if not bucket.strip():
        raise ValueError("R2 bucket is required")
    if not _VERSION_NAMESPACE.fullmatch(namespace):
        raise ValueError("namespace must be a complete version namespace such as reference/v1/")
    if namespace == PUBLIC_REFERENCE_PREFIX:
        raise ValueError(f"active reference namespace cannot be retired: {namespace}")

    keys = _list_all_keys(client, bucket, prefix=namespace)
    deleted: list[str] = []
    for key in keys:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise RuntimeError(f"failed to retire reference namespace while deleting {key}") from exc
        deleted.append(key)

    remaining = _list_all_keys(client, bucket, prefix=namespace)
    if remaining:
        preview = ", ".join(remaining[:5])
        raise RuntimeError(f"retired reference namespace still contains objects: {preview}")
    return {
        "status": "retired",
        "bucket": bucket,
        "namespace": namespace,
        "deleted_count": len(deleted),
        "deleted_keys": deleted,
    }


def retire_reference_namespace_from_env(namespace: str) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    return retire_reference_namespace(client_from_env(), bucket, namespace)


__all__ = [
    "audit_public_bucket",
    "audit_public_bucket_from_env",
    "retire_reference_namespace",
    "retire_reference_namespace_from_env",
]
