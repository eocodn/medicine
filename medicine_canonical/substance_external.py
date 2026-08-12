from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .substance_sources import (
    FDA_GSRS_UNII_NAMES_DATASET_KEY,
    FDA_GSRS_UNII_NAMES_FILENAME,
    OPENFDA_UNII_DATASET_KEY,
    OPENFDA_UNII_FILENAME,
    inspect_gsrs_unii_names_archive,
    inspect_unii_archive,
    iter_gsrs_unii_names,
)


TRUSTED_GSRS_NAME_TYPES = frozenset({"of", "cn", "sys"})


@dataclass
class ExternalEvidence:
    names: set[str]
    dataset_key: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_openfda_unii_snapshot(raw_dir: Path) -> tuple[list[dict], dict, Path]:
    path = raw_dir / OPENFDA_UNII_FILENAME
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"missing openFDA UNII snapshot or metadata under {raw_dir}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    required = {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
    missing = required - meta.keys()
    if missing:
        raise ValueError(f"invalid openFDA UNII metadata: missing {sorted(missing)}")
    if meta["dataset_key"] != OPENFDA_UNII_DATASET_KEY or meta["source_family"] != "openfda_unii":
        raise ValueError("openFDA UNII snapshot provenance mismatch")
    actual_sha = _sha256_file(path)
    if actual_sha != meta["sha256"]:
        raise ValueError(
            f"sha256 mismatch for openFDA UNII snapshot: expected {meta['sha256']}, got {actual_sha}"
        )
    records, archive_meta = inspect_unii_archive(path.read_bytes())
    if len(records) != int(meta["row_count"]):
        raise RuntimeError(
            f"openFDA UNII row-count mismatch: metadata {meta['row_count']}, archive {len(records)}"
        )
    merged = dict(meta)
    merged["archive_meta"] = archive_meta
    return records, merged, path


def load_gsrs_names_snapshot(raw_dir: Path) -> tuple[bytes, dict, Path]:
    path = raw_dir / FDA_GSRS_UNII_NAMES_FILENAME
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"missing FDA GSRS UNII Names snapshot or metadata under {raw_dir}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    required = {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
    missing = required - meta.keys()
    if missing:
        raise ValueError(f"invalid FDA GSRS UNII Names metadata: missing {sorted(missing)}")
    if (
        meta["dataset_key"] != FDA_GSRS_UNII_NAMES_DATASET_KEY
        or meta["source_family"] != "fda_gsrs_unii_names"
    ):
        raise ValueError("FDA GSRS UNII Names snapshot provenance mismatch")
    actual_sha = _sha256_file(path)
    if actual_sha != meta["sha256"]:
        raise ValueError(
            "sha256 mismatch for FDA GSRS UNII Names snapshot: "
            f"expected {meta['sha256']}, got {actual_sha}"
        )
    data = path.read_bytes()
    inspected = inspect_gsrs_unii_names_archive(data)
    if int(inspected["row_count"]) != int(meta["row_count"]):
        raise RuntimeError(
            "FDA GSRS UNII Names row-count mismatch: "
            f"metadata {meta['row_count']}, archive {inspected['row_count']}"
        )
    if meta.get("effective_date") and meta["effective_date"] != inspected["effective_date"]:
        raise RuntimeError(
            "FDA GSRS UNII Names effective-date mismatch: "
            f"metadata {meta['effective_date']}, archive {inspected['effective_date']}"
        )
    merged = dict(meta)
    merged["archive_member"] = inspected["member"]
    return data, merged, path


def build_external_index(
    preferred_records: list[dict],
    gsrs_names_data: bytes,
    normalize_name: Callable[[object], str],
) -> dict[str, dict[str, ExternalEvidence]]:
    index: dict[str, dict[str, ExternalEvidence]] = defaultdict(dict)

    def add(name: str, unii: str, dataset_key: str, *, preferred: bool = False) -> None:
        normalized = normalize_name(name)
        if not normalized or not unii:
            return
        existing = index[normalized].get(unii)
        if existing is None:
            index[normalized][unii] = ExternalEvidence({name}, dataset_key)
            return
        existing.names.add(name)
        if preferred:
            existing.dataset_key = dataset_key

    for row in iter_gsrs_unii_names(gsrs_names_data):
        if row["name_type"] not in TRUSTED_GSRS_NAME_TYPES:
            continue
        add(row["name"], row["unii"], FDA_GSRS_UNII_NAMES_DATASET_KEY)

    for row in preferred_records:
        add(
            str(row["substance_name"]).strip(),
            str(row["unii"]).strip(),
            OPENFDA_UNII_DATASET_KEY,
            preferred=True,
        )
    return index


__all__ = [
    "ExternalEvidence",
    "build_external_index",
    "load_gsrs_names_snapshot",
    "load_openfda_unii_snapshot",
]