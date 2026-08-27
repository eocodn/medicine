from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from medicine_reference.mfds_sources import MFDS_SOURCE_MANIFEST

from .job_lifecycle import JobLifecycle, fingerprint_inputs
from .schema import SCHEMA_VERSION
from .snapshot_io import snapshot_metadata_path
from .source_layout import MfdsSourceLayout
from .source_policy import CANONICAL_SOURCE_POLICY


CANONICAL_BUILD_JOB_VERSION = 1


def canonical_build_input_fingerprint(source_layout: MfdsSourceLayout) -> str:
    files: dict[str, Path] = {}
    for source in MFDS_SOURCE_MANIFEST:
        snapshot = source_layout.path_for(source)
        files[f"{source.dataset_key}:snapshot"] = snapshot
        files[f"{source.dataset_key}:metadata"] = snapshot_metadata_path(snapshot)
    return fingerprint_inputs(
        files,
        context={
            "job_version": CANONICAL_BUILD_JOB_VERSION,
            "schema_version": SCHEMA_VERSION,
            "source_policy": CANONICAL_SOURCE_POLICY,
        },
    )


def canonical_build_stage(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        uri = f"file:{path.resolve()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            row = con.execute(
                "SELECT value FROM canonical_meta WHERE key='build_stage'"
            ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"cannot inspect canonical build stage {path}: {exc}") from exc
    return str(row[0]) if row else None


def checkpoint_result(lifecycle: JobLifecycle, key: str) -> dict:
    value = lifecycle.artifacts.get(key)
    if not isinstance(value, dict):
        lifecycle.discard(f"checkpoint artifact {key!r} is missing or invalid")
    return value


__all__ = [
    "CANONICAL_BUILD_JOB_VERSION",
    "canonical_build_input_fingerprint",
    "canonical_build_stage",
    "checkpoint_result",
]