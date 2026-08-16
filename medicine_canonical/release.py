from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

PATCH_FORMAT = "medicine-chunk-v1"
_PATCH_MAGIC = b"MEDPATCH1"
_HEADER_LENGTH = struct.Struct(">I")
_RECORD_HEADER = struct.Struct(">QII")
DEFAULT_CHUNK_SIZE = 64 * 1024
RELEASE_PREFIX = "reference/v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            shutil.copyfile(source, temporary)
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


def compress_snapshot(source_path: str | Path, output_path: str | Path) -> dict:
    source = Path(source_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(f"snapshot source not found: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with source.open("rb") as src, temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as zipped:
                shutil.copyfileobj(src, zipped, length=1024 * 1024)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "compression": "gzip",
        "sha256": sha256_file(output),
        "size_bytes": output.stat().st_size,
        "uncompressed_sha256": sha256_file(source),
        "uncompressed_size_bytes": source.stat().st_size,
    }


def decompress_snapshot(source_path: str | Path, output_path: str | Path) -> dict:
    source = Path(source_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with gzip.open(source, "rb") as zipped, temporary.open("wb") as raw:
            shutil.copyfileobj(zipped, raw, length=1024 * 1024)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"sha256": sha256_file(output), "size_bytes": output.stat().st_size}


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
) -> dict:
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

    full_key = f"{RELEASE_PREFIX}/full/{target_sha}.sqlite.gz"
    full_path = root / full_key
    full = compress_snapshot(target, full_path)
    full_entry = {
        "key": full_key,
        "compression": "gzip",
        "sha256": full["sha256"],
        "size_bytes": full["size_bytes"],
    }

    patches: list[dict] = []
    patch_paths: list[Path] = []
    bases = list(previous_bases or [])
    if previous_db is not None:
        bases.insert(0, {"db_path": str(previous_db), "dataset_id": previous_dataset_id})
    seen_sources: set[str] = set()
    for base in bases:
        previous = Path(base["db_path"])
        previous_sha = sha256_file(previous)
        previous_size = previous.stat().st_size
        if previous_sha == target_sha or previous_sha in seen_sources:
            continue
        seen_sources.add(previous_sha)
        patch_key = f"{RELEASE_PREFIX}/patch/{previous_sha}-{target_sha}.mpatch"
        candidate = root / patch_key
        patch = create_chunk_patch(previous, target, candidate, chunk_size=chunk_size)
        verification_target = root / f".verify-{previous_sha[:12]}-{target_sha[:12]}.sqlite"
        try:
            apply_chunk_patch(previous, candidate, verification_target)
        finally:
            verification_target.unlink(missing_ok=True)
        if patch["patch_size_bytes"] < full["size_bytes"]:
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
            patch_paths.append(candidate)
        else:
            candidate.unlink(missing_ok=True)

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
        "history": list(history or []),
    }
    manifest_path = root / RELEASE_PREFIX / "latest.json"
    _write_json(manifest_path, manifest)
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
