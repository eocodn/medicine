from __future__ import annotations

import gzip
import hashlib
import json
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .job_lifecycle import JobLifecycle
from .reference_contracts.registry import VerifiedContractArtifact
from .release import (
    DEFAULT_CHUNK_SIZE,
    PATCH_FORMAT,
    apply_chunk_patch,
    compress_snapshot,
    create_chunk_patch,
    decompress_snapshot,
    sha256_file,
)
from .release_job import validate_checkpointed_file
from .release_r2_object_io import _download_to_file, _list_prefix_keys, _not_found


RELEASE_PREFIX = "reference/v2"
MAX_PATCH_BASES = 3
FULL_SNAPSHOT_RETENTION = 3
CONTRACT_PREPARE_JOB_VERSION = 1
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _prepare_contract_input_fingerprint(
    *,
    bucket: str,
    metadata: CandidateMetadata,
    bases: list[dict],
) -> str:
    payload = {
        "job_version": CONTRACT_PREPARE_JOB_VERSION,
        "bucket": bucket,
        "contract_major": metadata.candidate.contract_major,
        "dataset_id": metadata.dataset_id,
        "target_sha256": metadata.target_sha256,
        "target_size_bytes": metadata.target_size_bytes,
        "bases": bases,
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "release_prefix": RELEASE_PREFIX,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class ContractReleaseCandidate:
    contract_major: int
    database: Path
    manifest: Path
    verifier: Callable[[Path, int, str], object]

    def __init__(
        self,
        contract_major: int,
        database: str | Path,
        manifest: str | Path,
        *,
        verifier: Callable[[Path, int, str], object],
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
        if not callable(verifier):
            raise ValueError("reference contract candidate verifier is required")
        object.__setattr__(self, "verifier", verifier)


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
    skipped_bases: list[dict[str, str]] = field(default_factory=list)


class HistoricalBaseIntegrityError(RuntimeError):
    """A signed historical artifact was read authoritatively but failed integrity checks."""


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
    candidate.verifier(candidate.database, candidate.contract_major, dataset_id)
    return CandidateMetadata(
        candidate=candidate,
        dataset_id=dataset_id,
        target_sha256=target_sha256,
        target_size_bytes=target_size_bytes,
    )


def load_verified_artifact(artifact: VerifiedContractArtifact) -> CandidateMetadata:
    """Rebind one trusted build result to its exact final bytes.

    The artifact object never crosses an untrusted serialization boundary: it
    is created by the exporter and consumed by the publisher in the same
    process.  Recomputing SHA/size here detects any mutation after verification
    without repeating the million-row logical identity pass.
    """
    candidate = ContractReleaseCandidate(
        artifact.contract_major,
        artifact.database,
        artifact.manifest,
        verifier=lambda _database, _major, _dataset_id: None,
    )
    if not candidate.database.is_file() or not candidate.manifest.is_file():
        raise FileNotFoundError("verified contract artifact is missing")
    try:
        manifest = json.loads(candidate.manifest.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("verified contract manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("verified contract manifest must be an object")
    target_sha256 = sha256_file(candidate.database)
    target_size_bytes = candidate.database.stat().st_size
    if (
        artifact.dataset_id != manifest.get("dataset_id")
        or artifact.sha256 != manifest.get("sha256")
        or artifact.size_bytes != manifest.get("size_bytes")
        or artifact.contract_major != manifest.get("contract_major")
        or artifact.sha256 != target_sha256
        or artifact.size_bytes != target_size_bytes
    ):
        raise ValueError("verified contract artifact changed after contract verification")
    dataset_id = str(artifact.dataset_id).lower()
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError("verified contract dataset identity is invalid")
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
    *,
    progress=None,
    decompress_progress=None,
) -> tuple[Path, str]:
    full = base["full"]
    target = base["target"]
    archive = output_dir / "base.sqlite.gz"
    downloaded = _download_to_file(
        client,
        bucket,
        full["key"],
        archive,
        progress=progress,
    )
    if downloaded["sha256"] != full["sha256"] or downloaded["size_bytes"] != full["size_bytes"]:
        raise HistoricalBaseIntegrityError("remote contract full artifact does not match signed root")
    database = output_dir / "base.sqlite"
    try:
        decompressed = decompress_snapshot(
            archive,
            database,
            progress=decompress_progress,
            expected_size=target["size_bytes"],
        )
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise HistoricalBaseIntegrityError("remote contract full artifact is not valid gzip") from exc
    if decompressed["sha256"] != target["sha256"] or decompressed["size_bytes"] != target["size_bytes"]:
        raise HistoricalBaseIntegrityError("remote contract full target does not match signed root")
    return database, base["dataset_id"]


def prepare_contract(
    client,
    bucket: str,
    metadata: CandidateMetadata,
    previous_entry: dict | None,
    root: Path,
    temporary_root: Path,
    *,
    progress=None,
) -> PreparedContract:
    major = metadata.candidate.contract_major
    prefix = contract_prefix(major)
    full_key = f"{prefix}/full/{metadata.target_sha256}.sqlite.gz"
    full_path = root / full_key
    recent = recent_bases(previous_entry)
    checkpoint = root / prefix / ".prepare.checkpoint.json"
    lifecycle = JobLifecycle(
        f"contract-release-prepare-{major}",
        checkpoint,
        input_fingerprint=_prepare_contract_input_fingerprint(
            bucket=bucket,
            metadata=metadata,
            bases=recent,
        ),
        progress=progress,
        total_steps=2,
    )
    lifecycle.started()
    current_phase = "startup"

    def io_progress(phase: str):
        def report(processed: int, total: int) -> None:
            lifecycle.progress_update(phase, processed, total=total)
            lifecycle.heartbeat(phase)

        return report

    skipped_bases: list[dict[str, str]] = []
    try:
        phase = lifecycle.completed_phase
        if phase not in {None, "full", "bases"}:
            lifecycle.discard(f"unknown completed phase {phase!r}")

        if phase is None:
            current_phase = "full"
            lifecycle.step_started(current_phase, 1)
            compressed = compress_snapshot(
                metadata.candidate.database,
                full_path,
                progress=io_progress("full_compression"),
            )
            lifecycle.checkpoint(
                current_phase,
                {
                    "full_path": str(full_path),
                    "full_sha256": compressed["sha256"],
                    "full_size_bytes": compressed["size_bytes"],
                    "next_base_index": 0,
                    "history": [],
                    "patches": [],
                },
            )
            lifecycle.step_completed(current_phase, 1)
            phase = lifecycle.completed_phase
        else:
            if lifecycle.artifacts.get("full_path") != str(full_path):
                lifecycle.discard("checkpointed contract full path changed")
            validate_checkpointed_file(
                lifecycle,
                full_path,
                expected_sha256=lifecycle.artifacts.get("full_sha256"),
                expected_size=lifecycle.artifacts.get("full_size_bytes"),
                label="contract full artifact",
            )

        full = {
            "key": full_key,
            "compression": "gzip",
            "sha256": lifecycle.artifacts["full_sha256"],
            "size_bytes": lifecycle.artifacts["full_size_bytes"],
        }
        next_base_index = lifecycle.artifacts.get("next_base_index", 0)
        history_value = lifecycle.artifacts.get("history", [])
        patches_value = lifecycle.artifacts.get("patches", [])
        if not isinstance(next_base_index, int) or not 0 <= next_base_index <= len(recent):
            lifecycle.discard("checkpointed historical base index is invalid")
        if not isinstance(history_value, list) or not isinstance(patches_value, list):
            lifecycle.discard("checkpointed contract artifact lists are invalid")
        history = [dict(entry) for entry in history_value if isinstance(entry, dict)]
        patches = [dict(entry) for entry in patches_value if isinstance(entry, dict)]
        if len(history) != len(history_value) or len(patches) != len(patches_value):
            lifecycle.discard("checkpointed contract artifact entry is invalid")
        patch_paths: dict[str, Path] = {}
        for patch in patches:
            key = patch.get("key")
            if not isinstance(key, str) or not key:
                lifecycle.discard("checkpointed contract patch key is invalid")
            patch_path = root / key
            validate_checkpointed_file(
                lifecycle,
                patch_path,
                expected_sha256=patch.get("sha256"),
                expected_size=patch.get("size_bytes"),
                label=f"contract patch {key}",
            )
            patch_paths[key] = patch_path

        current_phase = "bases"
        lifecycle.step_started(current_phase, 2)
        seen_source_sha = {
            str(base["target"]["sha256"])
            for base in recent[:next_base_index]
            if str(base["target"]["sha256"]) != metadata.target_sha256
        }
        checkpoint_contiguous = True
        for index in range(next_base_index, len(recent)):
            base = recent[index]
            source_sha = str(base["target"]["sha256"])
            base_dir = temporary_root / f"contract-{major}-base-{index}"
            base_dir.mkdir(parents=True, exist_ok=True)
            lifecycle.heartbeat(f"base_download_{index + 1}", force=True)
            try:
                previous_db, previous_dataset_id = download_base(
                    client,
                    bucket,
                    base,
                    base_dir,
                    progress=io_progress(f"base_download_{index + 1}"),
                    decompress_progress=io_progress(f"base_decompress_{index + 1}"),
                )
            except Exception as exc:
                # Missing/corrupt history is authoritative for this observation,
                # but can be repaired externally before a retry. Do not advance
                # the durable prefix across it, so retries re-observe that state.
                if not (_not_found(exc) or isinstance(exc, HistoricalBaseIntegrityError)):
                    raise
                skipped_bases.append(
                    {
                        "key": str(base.get("full", {}).get("key") or ""),
                        "error": type(exc).__name__,
                    }
                )
                checkpoint_contiguous = False
                continue
            lifecycle.heartbeat(f"base_download_{index + 1}", force=True)
            if len(history) < FULL_SNAPSHOT_RETENTION - 1:
                history.append(base)
            if source_sha != metadata.target_sha256 and source_sha not in seen_source_sha:
                seen_source_sha.add(source_sha)
                patch_key = f"{prefix}/patch/{source_sha}-{metadata.target_sha256}.mpatch"
                patch_path = root / patch_key
                patch = create_chunk_patch(
                    previous_db,
                    metadata.candidate.database,
                    patch_path,
                    chunk_size=DEFAULT_CHUNK_SIZE,
                    progress=io_progress(f"patch_{index + 1}"),
                )
                verification = base_dir / "verified.sqlite"
                try:
                    apply_chunk_patch(
                        previous_db,
                        patch_path,
                        verification,
                        progress=lambda _processed, _total: lifecycle.heartbeat(
                            f"patch_verify_{index + 1}"
                        ),
                    )
                finally:
                    verification.unlink(missing_ok=True)
                if patch["patch_size_bytes"] < full["size_bytes"]:
                    patch_entry = {
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
                    patches.append(patch_entry)
                    patch_paths[patch_key] = patch_path
                else:
                    patch_path.unlink(missing_ok=True)
            if checkpoint_contiguous:
                lifecycle.checkpoint(
                    current_phase,
                    {
                        **lifecycle.artifacts,
                        "next_base_index": index + 1,
                        "history": history,
                        "patches": patches,
                    },
                )
        lifecycle.step_completed(current_phase, 2)
        lifecycle.completed()

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
            skipped_bases=skipped_bases,
        )
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        raise


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
