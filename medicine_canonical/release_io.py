from __future__ import annotations

import gzip
import hashlib
import os
import shutil
from pathlib import Path
from typing import BinaryIO, Callable


_IO_PROGRESS_INTERVAL_BYTES = 8 * 1024 * 1024

IoProgress = Callable[[int, int], None]


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def maybe_report_progress(
    progress: IoProgress | None,
    processed: int,
    total: int,
    last_reported: int,
) -> int:
    if progress is None:
        return last_reported
    complete = total > 0 and processed >= total
    if complete or processed - last_reported >= _IO_PROGRESS_INTERVAL_BYTES:
        progress(processed, total)
        return processed
    return last_reported


def copy_stream(
    source: BinaryIO,
    target: BinaryIO,
    *,
    total: int,
    progress: IoProgress | None,
) -> None:
    processed = 0
    last_reported = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        target.write(chunk)
        processed += len(chunk)
        last_reported = maybe_report_progress(progress, processed, total, last_reported)
    if progress is not None and processed != last_reported:
        progress(processed, total)


def compress_snapshot(
    source_path: str | Path,
    output_path: str | Path,
    *,
    progress: IoProgress | None = None,
) -> dict:
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
                copy_stream(
                    src,
                    zipped,
                    total=source.stat().st_size,
                    progress=progress,
                )
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


def decompress_snapshot(
    source_path: str | Path,
    output_path: str | Path,
    *,
    progress: IoProgress | None = None,
    expected_size: int | None = None,
) -> dict:
    source = Path(source_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with gzip.open(source, "rb") as zipped, temporary.open("wb") as raw:
            copy_stream(
                zipped,
                raw,
                total=expected_size if isinstance(expected_size, int) and expected_size >= 0 else 0,
                progress=progress,
            )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"sha256": sha256_file(output), "size_bytes": output.stat().st_size}


__all__ = [
    "IoProgress",
    "compress_snapshot",
    "copy_stream",
    "decompress_snapshot",
    "maybe_report_progress",
    "sha256_file",
]