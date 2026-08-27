from __future__ import annotations

import os
from pathlib import Path

from .release_io import IoProgress, maybe_report_progress, sha256_file


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class _ProgressReader:
    def __init__(self, handle, *, total: int, progress: IoProgress | None) -> None:
        self._handle = handle
        self._total = total
        self._progress = progress
        self._max_processed = 0
        self._last_reported = 0

    def read(self, size: int = -1):
        data = self._handle.read(size)
        position = self._handle.tell()
        self._max_processed = max(self._max_processed, position)
        self._last_reported = maybe_report_progress(
            self._progress,
            self._max_processed,
            self._total,
            self._last_reported,
        )
        if not data and self._progress is not None and self._max_processed != self._last_reported:
            self._progress(self._max_processed, self._total)
            self._last_reported = self._max_processed
        return data

    def __getattr__(self, name: str):
        return getattr(self._handle, name)


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _precondition_failed(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 412 or code in {"412", "PreconditionFailed"}


def _download_to_file(
    client,
    bucket: str,
    key: str,
    output: Path,
    *,
    progress: IoProgress | None = None,
) -> dict:
    response = client.get_object(Bucket=bucket, Key=key)
    total = response.get("ContentLength")
    if not isinstance(total, int) or total < 0:
        total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("wb") as handle:
            body = response["Body"]
            processed = 0
            last_reported = 0
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                processed += len(chunk)
                last_reported = maybe_report_progress(
                    progress,
                    processed,
                    total,
                    last_reported,
                )
            if progress is not None and processed != last_reported:
                progress(processed, total)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"sha256": sha256_file(output), "size_bytes": output.stat().st_size}


def _head_optional(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _not_found(exc):
            return None
        raise


def _verify_head(head: dict, *, size_bytes: int, sha256: str, key: str) -> None:
    if head.get("ContentLength") != size_bytes:
        raise RuntimeError(f"remote object size mismatch for {key}")
    if (head.get("Metadata") or {}).get("sha256") != sha256:
        raise RuntimeError(f"remote object hash metadata mismatch for {key}")


def _put_immutable(
    client,
    bucket: str,
    key: str,
    path: Path,
    *,
    content_type: str,
    progress: IoProgress | None = None,
) -> None:
    expected_size = path.stat().st_size
    expected_sha = sha256_file(path)
    existing = _head_optional(client, bucket, key)
    if existing is not None:
        _verify_head(existing, size_bytes=expected_size, sha256=expected_sha, key=key)
        return
    try:
        with path.open("rb") as handle:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=_ProgressReader(handle, total=expected_size, progress=progress),
                ContentType=content_type,
                CacheControl=IMMUTABLE_CACHE_CONTROL,
                Metadata={"sha256": expected_sha},
                custom_headers={"If-None-Match": "*"},
            )
    except Exception as exc:
        if not _precondition_failed(exc):
            raise
    head = client.head_object(Bucket=bucket, Key=key)
    _verify_head(head, size_bytes=expected_size, sha256=expected_sha, key=key)


def _list_prefix_keys(client, bucket: str, prefix: str) -> set[str]:
    keys: set[str] = set()
    continuation_token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = item.get("Key")
            if isinstance(key, str) and key.startswith(prefix):
                keys.add(key)
        if not response.get("IsTruncated"):
            return keys
        continuation_token = response.get("NextContinuationToken")
        if not isinstance(continuation_token, str) or not continuation_token:
            raise RuntimeError(
                f"remote object listing for {prefix} is truncated without continuation token"
            )


__all__ = [
    "IMMUTABLE_CACHE_CONTROL",
    "_download_to_file",
    "_head_optional",
    "_list_prefix_keys",
    "_not_found",
    "_precondition_failed",
    "_put_immutable",
    "_verify_head",
]