from __future__ import annotations

from pathlib import Path

from .job_lifecycle import JobLifecycle
from .release_io import sha256_file


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
    "validate_checkpointed_file",
]