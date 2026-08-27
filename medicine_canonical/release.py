from __future__ import annotations

import json
import os
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

from .job_lifecycle import JobLifecycle
from .release_io import (
    IoProgress,
    compress_snapshot,
    copy_stream,
    decompress_snapshot,
    maybe_report_progress,
    sha256_file,
)
from .release_job import release_prepare_fingerprint, validate_checkpointed_file

PATCH_FORMAT = "medicine-chunk-v1"
_PATCH_MAGIC = b"MEDPATCH1"
_HEADER_LENGTH = struct.Struct(">I")
_RECORD_HEADER = struct.Struct(">QII")
DEFAULT_CHUNK_SIZE = 64 * 1024
RELEASE_PREFIX = "reference/v1"
def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise EOFError(f"truncated patch: expected {size} bytes, got {len(data)}")
    return data


def create_chunk_patch(
    source_path: str | Path,
    target_path: str | Path,
    output_path: str | Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress: IoProgress | None = None,
) -> dict:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source = Path(source_path)
    target = Path(target_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(f"patch source not found: {source}")
    if not target.is_file():
        raise FileNotFoundError(f"patch target not found: {target}")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_size = source.stat().st_size
    target_size = target.stat().st_size
    source_sha = sha256_file(source)
    target_sha = sha256_file(target)
    header = {
        "format": PATCH_FORMAT,
        "source_sha256": source_sha,
        "source_size_bytes": source_size,
        "target_sha256": target_sha,
        "target_size_bytes": target_size,
        "chunk_size": chunk_size,
    }
    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(header_bytes) > 16 * 1024 * 1024:
        raise ValueError("patch header is unexpectedly large")

    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    changed_chunks = 0
    try:
        with source.open("rb") as old, target.open("rb") as new, temporary.open("wb") as patch:
            patch.write(_PATCH_MAGIC)
            patch.write(_HEADER_LENGTH.pack(len(header_bytes)))
            patch.write(header_bytes)
            chunk_index = 0
            processed = 0
            last_reported = 0
            while True:
                target_chunk = new.read(chunk_size)
                if not target_chunk:
                    break
                source_chunk = old.read(chunk_size)
                if source_chunk != target_chunk:
                    compressed = zlib.compress(target_chunk, level=9)
                    patch.write(_RECORD_HEADER.pack(chunk_index, len(target_chunk), len(compressed)))
                    patch.write(compressed)
                    changed_chunks += 1
                chunk_index += 1
                processed += len(target_chunk)
                last_reported = maybe_report_progress(
                    progress,
                    processed,
                    target_size,
                    last_reported,
                )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        **header,
        "changed_chunks": changed_chunks,
        "patch_sha256": sha256_file(output),
        "patch_size_bytes": output.stat().st_size,
    }


def _read_patch_header(handle: BinaryIO) -> dict:
    if _read_exact(handle, len(_PATCH_MAGIC)) != _PATCH_MAGIC:
        raise ValueError("unsupported patch magic")
    header_length = _HEADER_LENGTH.unpack(_read_exact(handle, _HEADER_LENGTH.size))[0]
    if header_length <= 0 or header_length > 16 * 1024 * 1024:
        raise ValueError("invalid patch header length")
    try:
        header = json.loads(_read_exact(handle, header_length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid patch header JSON") from exc
    if header.get("format") != PATCH_FORMAT:
        raise ValueError(f"unsupported patch format: {header.get('format')!r}")
    chunk_size = header.get("chunk_size")
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("invalid patch chunk size")
    for key in ("source_size_bytes", "target_size_bytes"):
        if not isinstance(header.get(key), int) or header[key] < 0:
            raise ValueError(f"invalid patch {key}")
    for key in ("source_sha256", "target_sha256"):
        value = header.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"invalid patch {key}")
    return header


def apply_chunk_patch(
    source_path: str | Path,
    patch_path: str | Path,
    output_path: str | Path,
    *,
    progress: IoProgress | None = None,
) -> dict:
    source = Path(source_path)
    patch = Path(patch_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(f"patch source not found: {source}")
    if not patch.is_file():
        raise FileNotFoundError(f"patch not found: {patch}")

    with patch.open("rb") as patch_handle:
        header = _read_patch_header(patch_handle)
        if source.stat().st_size != header["source_size_bytes"]:
            raise ValueError("patch source size does not match")
        if sha256_file(source) != header["source_sha256"]:
            raise ValueError("patch source SHA-256 does not match")

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.unlink(missing_ok=True)
        try:
            with source.open("rb") as original, temporary.open("wb") as rebuilt_copy:
                copy_stream(
                    original,
                    rebuilt_copy,
                    total=source.stat().st_size,
                    progress=progress,
                )
            with temporary.open("r+b") as rebuilt:
                rebuilt.truncate(header["target_size_bytes"])
                seen: set[int] = set()
                last_index = -1
                changed_chunks = 0
                while True:
                    prefix = patch_handle.read(_RECORD_HEADER.size)
                    if not prefix:
                        break
                    if len(prefix) != _RECORD_HEADER.size:
                        raise EOFError("truncated patch record header")
                    chunk_index, raw_length, compressed_length = _RECORD_HEADER.unpack(prefix)
                    if chunk_index in seen or chunk_index <= last_index:
                        raise ValueError("patch chunk indexes must be unique and ascending")
                    seen.add(chunk_index)
                    last_index = chunk_index
                    chunk_offset = chunk_index * header["chunk_size"]
                    if chunk_offset >= header["target_size_bytes"] and header["target_size_bytes"] != 0:
                        raise ValueError("patch chunk index is outside target")
                    expected_length = min(
                        header["chunk_size"],
                        max(0, header["target_size_bytes"] - chunk_offset),
                    )
                    if raw_length != expected_length or raw_length <= 0:
                        raise ValueError("patch chunk length does not match target geometry")
                    if compressed_length <= 0:
                        raise ValueError("invalid compressed patch chunk length")
                    compressed = _read_exact(patch_handle, compressed_length)
                    try:
                        payload = zlib.decompress(compressed)
                    except zlib.error as exc:
                        raise ValueError("invalid compressed patch chunk") from exc
                    if len(payload) != raw_length:
                        raise ValueError("decompressed patch chunk length mismatch")
                    rebuilt.seek(chunk_offset)
                    rebuilt.write(payload)
                    changed_chunks += 1
                rebuilt.flush()
                os.fsync(rebuilt.fileno())
            actual_size = temporary.stat().st_size
            actual_sha = sha256_file(temporary)
            if actual_size != header["target_size_bytes"]:
                raise RuntimeError("rebuilt target size does not match patch target")
            if actual_sha != header["target_sha256"]:
                raise RuntimeError("rebuilt target SHA-256 does not match patch target")
            os.replace(temporary, output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    return {
        "format": PATCH_FORMAT,
        "sha256": header["target_sha256"],
        "size_bytes": header["target_size_bytes"],
        "changed_chunks": changed_chunks,
    }


def prepare_release(
    target_db: str | Path,
    mobile_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    previous_db: str | Path | None = None,
    previous_dataset_id: str | None = None,
    previous_bases: list[dict] | None = None,
    history: list[dict] | None = None,
    created_at: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress=None,
) -> dict:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    target = Path(target_db)
    mobile_manifest_file = Path(mobile_manifest_path)
    root = Path(output_dir)
    mobile_manifest = json.loads(mobile_manifest_file.read_text(encoding="utf-8"))
    target_sha = sha256_file(target)
    target_size = target.stat().st_size
    if mobile_manifest.get("sha256") != target_sha:
        raise ValueError("mobile manifest SHA-256 does not match target DB")
    if mobile_manifest.get("size_bytes") != target_size:
        raise ValueError("mobile manifest size does not match target DB")
    dataset_id = mobile_manifest.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("mobile manifest dataset_id is required")

    raw_bases = list(previous_bases or [])
    if previous_db is not None:
        raw_bases.insert(0, {"db_path": str(previous_db), "dataset_id": previous_dataset_id})
    bases: list[dict] = []
    for base in raw_bases:
        previous = Path(base["db_path"])
        if not previous.is_file():
            raise FileNotFoundError(f"patch source not found: {previous}")
        bases.append(
            {
                "db_path": str(previous),
                "dataset_id": base.get("dataset_id"),
                "sha256": sha256_file(previous),
                "size_bytes": previous.stat().st_size,
            }
        )
    release_history = list(history or [])
    input_fingerprint = release_prepare_fingerprint(
        target_sha256=target_sha,
        target_size=target_size,
        dataset_id=dataset_id,
        schema_version=mobile_manifest.get("schema_version"),
        bases=bases,
        history=release_history,
        chunk_size=chunk_size,
        release_prefix=RELEASE_PREFIX,
        patch_format=PATCH_FORMAT,
    )

    full_key = f"{RELEASE_PREFIX}/full/{target_sha}.sqlite.gz"
    full_path = root / full_key
    checkpoint = root / RELEASE_PREFIX / ".prepare-release.checkpoint.json"
    lifecycle = JobLifecycle(
        "release-prepare",
        checkpoint,
        input_fingerprint=input_fingerprint,
        progress=progress,
        total_steps=3,
    )
    lifecycle.started()
    current_phase = "startup"

    def io_progress(phase: str) -> IoProgress:
        def report(processed: int, total: int) -> None:
            lifecycle.progress_update(phase, processed, total=total)
            lifecycle.heartbeat(phase)

        return report

    try:
        phase = lifecycle.completed_phase
        if phase not in {None, "full", "patches"}:
            lifecycle.discard(f"unknown completed phase {phase!r}")

        if phase is None:
            current_phase = "full"
            lifecycle.step_started(current_phase, 1)
            full = compress_snapshot(
                target,
                full_path,
                progress=io_progress("full_compression"),
            )
            lifecycle.checkpoint(
                current_phase,
                {
                    "full_path": str(full_path),
                    "full_sha256": full["sha256"],
                    "full_size_bytes": full["size_bytes"],
                    "next_base_index": 0,
                    "patches": [],
                },
            )
            lifecycle.step_completed(current_phase, 1)
            phase = lifecycle.completed_phase
        else:
            if lifecycle.artifacts.get("full_path") != str(full_path):
                lifecycle.discard("checkpointed full artifact path changed")
            validate_checkpointed_file(
                lifecycle,
                full_path,
                expected_sha256=lifecycle.artifacts.get("full_sha256"),
                expected_size=lifecycle.artifacts.get("full_size_bytes"),
                label="full artifact",
            )

        full_entry = {
            "key": full_key,
            "compression": "gzip",
            "sha256": lifecycle.artifacts["full_sha256"],
            "size_bytes": lifecycle.artifacts["full_size_bytes"],
        }
        next_base_index = lifecycle.artifacts.get("next_base_index", 0)
        checkpointed_patches = lifecycle.artifacts.get("patches", [])
        if not isinstance(next_base_index, int) or not 0 <= next_base_index <= len(bases):
            lifecycle.discard("checkpointed patch base index is invalid")
        if not isinstance(checkpointed_patches, list):
            lifecycle.discard("checkpointed patch list is invalid")
        patches = [dict(entry) for entry in checkpointed_patches if isinstance(entry, dict)]
        if len(patches) != len(checkpointed_patches):
            lifecycle.discard("checkpointed patch entry is invalid")
        for patch in patches:
            key = patch.get("key")
            if not isinstance(key, str) or not key:
                lifecycle.discard("checkpointed patch key is invalid")
            validate_checkpointed_file(
                lifecycle,
                root / key,
                expected_sha256=patch.get("sha256"),
                expected_size=patch.get("size_bytes"),
                label=f"patch artifact {key}",
            )

        current_phase = "patches"
        lifecycle.step_started(current_phase, 2)
        seen_sources: set[str] = set()
        for base in bases[:next_base_index]:
            previous_sha = str(base["sha256"])
            if previous_sha != target_sha:
                seen_sources.add(previous_sha)
        for index in range(next_base_index, len(bases)):
            base = bases[index]
            previous = Path(base["db_path"])
            previous_sha = str(base["sha256"])
            previous_size = int(base["size_bytes"])
            if previous_sha != target_sha and previous_sha not in seen_sources:
                seen_sources.add(previous_sha)
                patch_key = f"{RELEASE_PREFIX}/patch/{previous_sha}-{target_sha}.mpatch"
                candidate = root / patch_key
                patch = create_chunk_patch(
                    previous,
                    target,
                    candidate,
                    chunk_size=chunk_size,
                    progress=io_progress(f"patch_{index + 1}"),
                )
                verification_target = (
                    root / f".verify-{previous_sha[:12]}-{target_sha[:12]}.sqlite"
                )
                try:
                    apply_chunk_patch(
                        previous,
                        candidate,
                        verification_target,
                        progress=lambda _processed, _total: lifecycle.heartbeat(
                            f"patch_verify_{index + 1}"
                        ),
                    )
                finally:
                    verification_target.unlink(missing_ok=True)
                if patch["patch_size_bytes"] < full_entry["size_bytes"]:
                    patches.append(
                        {
                            "key": patch_key,
                            "format": PATCH_FORMAT,
                            "chunk_size": chunk_size,
                            "from_dataset_id": base.get("dataset_id"),
                            "from_sha256": previous_sha,
                            "from_size_bytes": previous_size,
                            "sha256": patch["patch_sha256"],
                            "size_bytes": patch["patch_size_bytes"],
                            "changed_chunks": patch["changed_chunks"],
                        }
                    )
                else:
                    candidate.unlink(missing_ok=True)
            lifecycle.checkpoint(
                current_phase,
                {
                    **lifecycle.artifacts,
                    "next_base_index": index + 1,
                    "patches": patches,
                },
            )
        if lifecycle.completed_phase == "full":
            lifecycle.checkpoint(
                current_phase,
                {
                    **lifecycle.artifacts,
                    "next_base_index": len(bases),
                    "patches": patches,
                },
            )
        lifecycle.step_completed(current_phase, 2)

        current_phase = "manifest"
        lifecycle.step_started(current_phase, 3)
        manifest = {
            "schema_version": 1,
            "created_at": created_at,
            "dataset_id": dataset_id,
            "target": {
                "schema_version": str(mobile_manifest.get("schema_version")),
                "sha256": target_sha,
                "size_bytes": target_size,
            },
            "full": full_entry,
            "patches": patches,
            "history": release_history,
        }
        manifest_path = root / RELEASE_PREFIX / "latest.json"
        _write_json(manifest_path, manifest)
        lifecycle.step_completed(current_phase, 3)
        lifecycle.completed()
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        raise

    patch_paths = [root / patch["key"] for patch in patches]
    return {
        "manifest_path": str(manifest_path),
        "full_path": str(full_path),
        "patch_path": str(patch_paths[0]) if patch_paths else None,
        "patch_paths": [str(path) for path in patch_paths],
        "manifest": manifest,
    }


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "PATCH_FORMAT",
    "RELEASE_PREFIX",
    "apply_chunk_patch",
    "compress_snapshot",
    "create_chunk_patch",
    "decompress_snapshot",
    "prepare_release",
    "sha256_file",
]
