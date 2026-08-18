from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Mapping


REQUIRED_SNAPSHOT_METADATA_FIELDS = frozenset(
    {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def sha256_file(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_metadata_path(snapshot_path: str | Path) -> Path:
    path = Path(snapshot_path)
    return path.with_suffix(path.suffix + ".meta.json")


def load_snapshot_metadata(snapshot_path: str | Path, *, label: str) -> dict:
    path = Path(snapshot_path)
    metadata_path = snapshot_metadata_path(path)
    if not metadata_path.exists():
        raise FileNotFoundError(f"missing {label} metadata: {metadata_path}")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {label} metadata {metadata_path}: expected object")
    missing = REQUIRED_SNAPSHOT_METADATA_FIELDS - payload.keys()
    if missing:
        raise ValueError(f"invalid {label} metadata {metadata_path}: missing {sorted(missing)}")
    actual_sha = sha256_file(path)
    if actual_sha != payload["sha256"]:
        raise ValueError(
            f"sha256 mismatch for {label} {path}: expected {payload['sha256']}, got {actual_sha}"
        )
    return payload


def insert_source_snapshot(
    con: sqlite3.Connection,
    metadata: Mapping[str, object],
    snapshot_path: str | Path,
) -> None:
    con.execute(
        """
        INSERT INTO source_snapshots(
            dataset_key,source_family,source_locator,snapshot_path,fetched_at,
            row_count,reported_row_count,sha256,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            metadata["dataset_key"],
            metadata["source_family"],
            metadata["source_locator"],
            str(snapshot_path),
            metadata.get("fetched_at"),
            int(metadata["row_count"]),
            int(metadata.get("reported_row_count") or 0),
            metadata["sha256"],
            canonical_json(metadata),
        ),
    )


__all__ = [
    "REQUIRED_SNAPSHOT_METADATA_FIELDS",
    "canonical_json",
    "insert_source_snapshot",
    "load_snapshot_metadata",
    "sha256_file",
    "snapshot_metadata_path",
]