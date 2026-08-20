from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .release import (
    DEFAULT_CHUNK_SIZE,
    PATCH_FORMAT,
    apply_chunk_patch,
    compress_snapshot,
    create_chunk_patch,
    decompress_snapshot,
    sha256_file,
)
from .release_r2 import _download_to_file, _list_prefix_keys


RELEASE_PREFIX = "reference/v2"
MAX_PATCH_BASES = 3
FULL_SNAPSHOT_RETENTION = 3
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ContractReleaseCandidate:
    contract_major: int
    database: Path
    manifest: Path

    def __init__(
        self,
        contract_major: int,
        database: str | Path,
        manifest: str | Path,
    ) -> None:
        if (
            not isinstance(contract_major, int)
            or isinstance(contract_major, bool)
            or contract_major <= 0
        ):
            raise ValueError("contract major must be a positive integer")
        object.__setattr__(self, "contract_major", contract_major)
        object.__setattr__(self, "database", Path(database))
        object.__setattr__(self, "manifest", Path(manifest))


@dataclass(frozen=True)
class CandidateMetadata:
    candidate: ContractReleaseCandidate
    dataset_id: str
    target_sha256: str
    target_size_bytes: int


@dataclass
class PreparedContract:
    entry: dict
    full_path: Path | None
    patch_paths: dict[str, Path]


def contract_prefix(contract_major: int) -> str:
    return f"{RELEASE_PREFIX}/contracts/{contract_major}"


def full_prefix(contract_major: int) -> str:
    return f"{contract_prefix(contract_major)}/full/"


def patch_prefix(contract_major: int) -> str:
    return f"{contract_prefix(contract_major)}/patch/"


def load_candidate(candidate: ContractReleaseCandidate) -> CandidateMetadata:
    if not candidate.database.is_file():
        raise FileNotFoundError(f"reference contract database not found: {candidate.database}")
    if not candidate.manifest.is_file():
        raise FileNotFoundError(f"reference contract manifest not found: {candidate.manifest}")
    try:
        manifest = json.loads(candidate.manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("reference contract manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("reference contract manifest must be an object")
    if manifest.get("contract_major") != candidate.contract_major:
        raise ValueError("reference contract manifest major does not match candidate")
    dataset_id = str(manifest.get("dataset_id") or "").lower()
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError("reference contract manifest dataset identity is invalid")
    target_sha256 = sha256_file(candidate.database)
    target_size_bytes = candidate.database.stat().st_size
    if manifest.get("sha256") != target_sha256:
        raise ValueError("reference contract manifest SHA-256 does not match database")
    if manifest.get("size_bytes") != target_size_bytes:
        raise ValueError("reference contract manifest size does not match database")
    return CandidateMetadata(
        candidate=candidate,
        dataset_id=dataset_id,
        target_sha256=target_sha256,
        target_size_bytes=target_size_bytes,
    )


def snapshot_entry(entry: dict) -> dict:
    return {
        "dataset_id": entry["dataset_id"],
        "target": dict(entry["target"]),
        "full": dict(entry["full"]),
    }


def recent_bases(entry: dict | None) -> list[dict]:
    if entry is None:
        return []
    bases = [snapshot_entry(entry)]
    history = entry.get("history") or []
    if not isinstance(history, list):
        raise ValueError("remote contract history must be a list")
    for item in history:
        if not isinstance(item, dict):
            raise ValueError("remote contract history entry is invalid")
        bases.append(snapshot_entry(item))
        if len(bases) >= MAX_PATCH_BASES:
            break
    return bases


def download_base(
    client,
    bucket: str,
    base: dict,
    output_dir: Path,
) -> tuple[Path, str]:
    full = base["full"]
    target = base["target"]
    archive = output_dir / "base.sqlite.gz"
    downloaded = _download_to_file(client, bucket, full["key"], archive)
    if downloaded["sha256"] != full["sha256"] or downloaded["size_bytes"] != full["size_bytes"]:
        raise RuntimeError("remote contract full artifact does not match signed root")
    database = output_dir / "base.sqlite"
    decompressed = decompress_snapshot(archive, database)
    if decompressed["sha256"] != target["sha256"] or decompressed["size_bytes"] != target["size_bytes"]:
        raise RuntimeError("remote contract full target does not match signed root")
    return database, base["dataset_id"]


def prepare_contract(
    client,
    bucket: str,
    metadata: CandidateMetadata,
    previous_entry: dict | None,
    root: Path,
    temporary_root: Path,
) -> PreparedContract:
    major = metadata.candidate.contract_major
    prefix = contract_prefix(major)
    full_key = f"{prefix}/full/{metadata.target_sha256}.sqlite.gz"
    full_path = root / full_key
    compressed = compress_snapshot(metadata.candidate.database, full_path)
    full = {
        "key": full_key,
        "compression": "gzip",
        "sha256": compressed["sha256"],
        "size_bytes": compressed["size_bytes"],
    }

    recent = recent_bases(previous_entry)
    history = recent[: FULL_SNAPSHOT_RETENTION - 1]
    patches: list[dict] = []
    patch_paths: dict[str, Path] = {}
    seen_source_sha: set[str] = set()
    for index, base in enumerate(recent):
        source_sha = str(base["target"]["sha256"])
        if source_sha == metadata.target_sha256 or source_sha in seen_source_sha:
            continue
        seen_source_sha.add(source_sha)
        base_dir = temporary_root / f"contract-{major}-base-{index}"
        base_dir.mkdir(parents=True, exist_ok=True)
        previous_db, previous_dataset_id = download_base(client, bucket, base, base_dir)
        patch_key = f"{prefix}/patch/{source_sha}-{metadata.target_sha256}.mpatch"
        patch_path = root / patch_key
        patch = create_chunk_patch(
            previous_db,
            metadata.candidate.database,
            patch_path,
            chunk_size=DEFAULT_CHUNK_SIZE,
        )
        verification = base_dir / "verified.sqlite"
        try:
            apply_chunk_patch(previous_db, patch_path, verification)
        finally:
            verification.unlink(missing_ok=True)
        if patch["patch_size_bytes"] >= compressed["size_bytes"]:
            patch_path.unlink(missing_ok=True)
            continue
        patches.append(
            {
                "key": patch_key,
                "format": PATCH_FORMAT,
                "chunk_size": DEFAULT_CHUNK_SIZE,
                "from_dataset_id": previous_dataset_id,
                "from_sha256": source_sha,
                "from_size_bytes": base["target"]["size_bytes"],
                "sha256": patch["patch_sha256"],
                "size_bytes": patch["patch_size_bytes"],
                "changed_chunks": patch["changed_chunks"],
            }
        )
        patch_paths[patch_key] = patch_path

    return PreparedContract(
        entry={
            "dataset_id": metadata.dataset_id,
            "target": {
                "sha256": metadata.target_sha256,
                "size_bytes": metadata.target_size_bytes,
            },
            "full": full,
            "patches": patches,
            "history": history,
        },
        full_path=full_path,
        patch_paths=patch_paths,
    )


def entry_keys(contract_major: int, entry: dict) -> set[str]:
    expected_full_prefix = full_prefix(contract_major)
    expected_patch_prefix = patch_prefix(contract_major)
    keys: set[str] = set()

    def add_full(full: object) -> None:
        if not isinstance(full, dict) or not isinstance(full.get("key"), str):
            raise ValueError("contract release entry has invalid full snapshot")
        key = full["key"]
        if not key.startswith(expected_full_prefix):
            raise ValueError("contract release full snapshot escaped contract namespace")
        keys.add(key)

    add_full(entry.get("full"))
    for history in entry.get("history") or []:
        if not isinstance(history, dict):
            raise ValueError("contract release history entry is invalid")
        add_full(history.get("full"))
    for patch in entry.get("patches") or []:
        if not isinstance(patch, dict) or not isinstance(patch.get("key"), str):
            raise ValueError("contract release patch entry is invalid")
        key = patch["key"]
        if not key.startswith(expected_patch_prefix):
            raise ValueError("contract release patch escaped contract namespace")
        keys.add(key)
    return keys


def contract_inventory(client, bucket: str, contract_major: int) -> set[str]:
    return _list_prefix_keys(client, bucket, full_prefix(contract_major)) | _list_prefix_keys(
        client,
        bucket,
        patch_prefix(contract_major),
    )


__all__ = [
    "CandidateMetadata",
    "ContractReleaseCandidate",
    "FULL_SNAPSHOT_RETENTION",
    "MAX_PATCH_BASES",
    "PreparedContract",
    "RELEASE_PREFIX",
    "contract_inventory",
    "contract_prefix",
    "entry_keys",
    "full_prefix",
    "load_candidate",
    "patch_prefix",
    "prepare_contract",
]