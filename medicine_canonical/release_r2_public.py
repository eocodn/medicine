from __future__ import annotations

import os

from .release_r2 import LATEST_KEY, RELEASE_PREFIX, client_from_env


def _list_all_keys(client, bucket: str) -> list[str]:
    keys: list[str] = []
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, str] = {"Bucket": bucket}
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
    unexpected = [key for key in keys if not key.startswith(f"{RELEASE_PREFIX}/")]
    if unexpected:
        preview = ", ".join(unexpected[:5])
        raise RuntimeError(
            f"R2 bucket contains objects outside {RELEASE_PREFIX}/ and cannot be exposed: {preview}"
        )
    if LATEST_KEY not in keys:
        raise RuntimeError(f"R2 bucket does not contain required {LATEST_KEY}")
    return {
        "status": "safe_to_expose",
        "bucket": bucket,
        "object_count": len(keys),
        "unexpected_keys": [],
        "latest_key": LATEST_KEY,
    }


def audit_public_bucket_from_env() -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    return audit_public_bucket(client_from_env(), bucket)


__all__ = ["audit_public_bucket", "audit_public_bucket_from_env"]