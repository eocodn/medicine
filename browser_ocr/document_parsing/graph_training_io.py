from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import paddle

from .graph_encoder_paddle import SparseDocumentGraphEncoder


STATE_FILE = "training-state.json"
RESULT_FILE = "result.json"
LOCK_FILE = ".parser-graph-training.lock"
CHECKPOINTS_DIR = "checkpoints"


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read graph training JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"graph training JSON must contain an object: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(canonical_json(value) + b"\n")
    os.replace(temporary, path)


def atomic_checkpoint(
    root: Path,
    *,
    epoch: int,
    profile_sha256: str,
    model: SparseDocumentGraphEncoder,
    optimizer: paddle.optimizer.Optimizer,
    training_loss: float,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoints = root / CHECKPOINTS_DIR
    checkpoints.mkdir(parents=True, exist_ok=True)
    final = checkpoints / f"epoch-{epoch:04d}"
    if final.exists():
        raise ValueError(f"graph training checkpoint already exists unexpectedly: {final}")
    temporary = checkpoints / f".epoch-{epoch:04d}.tmp-{os.getpid()}"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    try:
        model_path = temporary / "model.pdparams"
        optimizer_path = temporary / "optimizer.pdopt"
        paddle.save(model.state_dict(), str(model_path))
        paddle.save(optimizer.state_dict(), str(optimizer_path))
        record = {
            "schema_version": 1,
            "epoch": epoch,
            "profile_sha256": profile_sha256,
            "training_loss": training_loss,
            "validation": dict(validation),
            "model_sha256": sha256_file(model_path),
            "optimizer_sha256": sha256_file(optimizer_path),
        }
        atomic_json(temporary / "checkpoint.json", record)
        os.replace(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "epoch": epoch,
        "training_loss": training_loss,
        "validation": dict(validation),
        "checkpoint": str(final / "model.pdparams"),
        "model_sha256": record["model_sha256"],
        "optimizer_sha256": record["optimizer_sha256"],
    }


def checkpoint_record(root: Path, epoch: int, profile_sha256: str) -> dict[str, Any] | None:
    directory = root / CHECKPOINTS_DIR / f"epoch-{epoch:04d}"
    if not directory.exists():
        return None
    manifest = directory / "checkpoint.json"
    model = directory / "model.pdparams"
    optimizer = directory / "optimizer.pdopt"
    if not manifest.is_file() or not model.is_file() or not optimizer.is_file():
        raise ValueError(f"graph training checkpoint {epoch} is incomplete")
    raw = json_file(manifest)
    if raw.get("epoch") != epoch or raw.get("profile_sha256") != profile_sha256:
        raise ValueError(f"graph training checkpoint {epoch} profile mismatch")
    if raw.get("model_sha256") != sha256_file(model) or raw.get("optimizer_sha256") != sha256_file(optimizer):
        raise ValueError(f"graph training checkpoint {epoch} SHA-256 mismatch")
    validation = raw.get("validation")
    if not isinstance(validation, Mapping):
        raise ValueError(f"graph training checkpoint {epoch} validation metrics are invalid")
    return {
        "epoch": epoch,
        "training_loss": float(raw["training_loss"]),
        "validation": dict(validation),
        "checkpoint": str(model),
        "model_sha256": str(raw["model_sha256"]),
        "optimizer_sha256": str(raw["optimizer_sha256"]),
    }


def adopt_checkpoints(
    root: Path,
    state: dict[str, Any],
    profile_sha256: str,
    epochs: int,
) -> list[dict[str, Any]]:
    history = list(state.get("history") or [])
    completed = int(state.get("completed_epoch") or 0)
    if len(history) != completed:
        raise ValueError("graph training state history disagrees with completed_epoch")
    for epoch in range(1, completed + 1):
        persisted = checkpoint_record(root, epoch, profile_sha256)
        if persisted is None or persisted != history[epoch - 1]:
            raise ValueError(f"graph training state disagrees with checkpoint {epoch}")
    next_epoch = completed + 1
    while next_epoch <= epochs:
        persisted = checkpoint_record(root, next_epoch, profile_sha256)
        if persisted is None:
            break
        history.append(persisted)
        completed = next_epoch
        next_epoch += 1
    state["completed_epoch"] = completed
    state["history"] = history
    return history


def load_latest_checkpoint(
    root: Path,
    history: Sequence[Mapping[str, Any]],
    model: SparseDocumentGraphEncoder,
    optimizer: paddle.optimizer.Optimizer,
) -> None:
    if not history:
        return
    epoch = int(history[-1]["epoch"])
    directory = root / CHECKPOINTS_DIR / f"epoch-{epoch:04d}"
    model.set_state_dict(paddle.load(str(directory / "model.pdparams")))
    optimizer.set_state_dict(paddle.load(str(directory / "optimizer.pdopt")))


def validate_completed_result(
    root: Path,
    profile: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    result_path = root / RESULT_FILE
    if not result_path.is_file():
        raise ValueError("completed graph training state is missing result.json")
    expected_result_sha = str(state.get("result_sha256") or "")
    if expected_result_sha != sha256_file(result_path):
        raise ValueError("completed graph training result SHA-256 mismatch")
    result = json_file(result_path)
    if result.get("profile") != profile or result.get("status") != "ok":
        raise ValueError("completed graph training state/result profile mismatch")
    checkpoint = Path(str(result.get("best_checkpoint") or ""))
    if not checkpoint.is_file() or sha256_file(checkpoint) != result.get("best_checkpoint_sha256"):
        raise ValueError("completed graph training best checkpoint SHA-256 mismatch")
    return result


__all__ = [
    "LOCK_FILE",
    "RESULT_FILE",
    "STATE_FILE",
    "adopt_checkpoints",
    "atomic_checkpoint",
    "atomic_json",
    "canonical_json",
    "json_file",
    "load_latest_checkpoint",
    "sha256_bytes",
    "sha256_file",
    "validate_completed_result",
]