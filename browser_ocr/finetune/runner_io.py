from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .dataset import DatasetError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"JSON file must contain an object: {path}")
    return value


def write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def verify_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise DatasetError(f"{label} does not exist: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise DatasetError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def stream_command(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    capture: bool = True,
    append: bool = False,
    echo: bool = True,
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []
    with log_path.open("a" if append else "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if echo:
                print(line, end="", flush=True)
            log.write(line)
            log.flush()
            if capture:
                captured.append(line)
        process.stdout.close()
        return_code = process.wait()
    if return_code != 0:
        raise DatasetError(f"command failed with exit code {return_code}: {command[1]}")
    return "".join(captured)
