from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .job_lifecycle import JobLifecycle
from .release_io import sha256_file


RELEASE_PREPARE_JOB_VERSION = 1


def release_prepare_fingerprint(
    *,
    target_sha256: str,
    target_size: int,
    dataset_id: str,
    schema_version: object,
    bases: list[dict],
    history: list[dict],
    chunk_size: int,
    release_prefix: str,
    patch_format: str,
) -> str:
    payload = {
        "job_version": RELEASE_PREPARE_JOB_VERSION,
        "target_sha256": target_sha256,
        "target_size_bytes": target_size,
        "dataset_id": dataset_id,
        "schema_version": schema_version,
        "bases": [
            {
                "sha256": base["sha256"],
                "size_bytes": base["size_bytes"],
                "dataset_id": base.get("dataset_id"),
            }
            for base in bases
        ],
        "history": history,
        "chunk_size": chunk_size,
        "release_prefix": release_prefix,
        "patch_format": patch_format,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_checkpointed_file(
    lifecycle: JobLifecycle,
    path: Path,
    *,
    expected_sha256: object,
    expected_size: object,
    label: str,
) -> None:
    if not isinstance(expected_sha256, str) or not isinstance(expected_size, int):
        lifecycle.discard(f"{label} checkpoint identity is missing")
    if not path.is_file():
        lifecycle.discard(f"{label} checkpoint file is missing")
    if path.stat().st_size != expected_size or sha256_file(path) != expected_sha256:
        lifecycle.discard(f"{label} checkpoint bytes changed")


__all__ = [
    "RELEASE_PREPARE_JOB_VERSION",
    "release_prepare_fingerprint",
    "validate_checkpointed_file",
]