from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .artifact_contract import normalize_dataset_state, strict_json_loads


LOCK_FILE = ".dataset.lock"
STATE_FILE = ".dataset-state.json"


class ArtifactStorageError(ValueError):
    pass


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(content)
    os.replace(temporary, path)


@contextmanager
def exclusive_output_lock(root: Path) -> Iterator[None]:
    lock_path = root / LOCK_FILE
    stream = lock_path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArtifactStorageError(f"parser dataset build is already active in {root}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def read_dataset_state(path: Path) -> Mapping[str, Any]:
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"), label="parser dataset output state")
        return normalize_dataset_state(value)
    except (OSError, ValueError) as exc:
        raise ArtifactStorageError(str(exc)) from exc


__all__ = [
    "ArtifactStorageError",
    "LOCK_FILE",
    "STATE_FILE",
    "atomic_write",
    "exclusive_output_lock",
    "read_dataset_state",
]