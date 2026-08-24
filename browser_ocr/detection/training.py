from __future__ import annotations

import fcntl
import re
import selectors
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Mapping

from .training_contract import (
    DetectorTrainingConfig,
    DetectorTrainingError,
    _json_object,
    _load_inputs,
    _now,
    _require_sha256,
    _sha256_file,
    _training_command,
    _training_overrides,
    _verify_file,
    _write_json_atomic,
    prepare_detector_training,
)


ProgressCallback = Callable[[int, str], None]


def _checkpoint_prefix(model_dir: Path, epoch: int) -> Path:
    return model_dir / f"iter_epoch_{epoch}"


def _checkpoint_complete(prefix: Path) -> bool:
    return all(Path(str(prefix) + suffix).is_file() for suffix in (".pdparams", ".pdopt", ".states"))


def _find_resume_checkpoint(model_dir: Path, *, maximum_epoch: int) -> tuple[int, Path] | None:
    candidates: list[tuple[int, Path]] = []
    for params_path in model_dir.glob("iter_epoch_*.pdparams"):
        match = re.fullmatch(r"iter_epoch_(\d+)\.pdparams", params_path.name)
        if match is None:
            continue
        epoch = int(match.group(1))
        prefix = params_path.with_suffix("")
        if 0 < epoch <= maximum_epoch and _checkpoint_complete(prefix):
            candidates.append((epoch, prefix))
    return max(candidates, default=None, key=lambda item: item[0])


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DetectorTrainingError(f"detector training is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


_EPOCH_PATTERN = re.compile(r"epoch:\s*\[(\d+)/(\d+)\]", re.IGNORECASE)


def _stream_training(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    on_progress: ProgressCallback,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    last_epoch = 0
    last_heartbeat = time.monotonic()
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        while process.poll() is None:
            events = selector.select(timeout=5)
            if events:
                line = process.stdout.readline()
                if line:
                    sys.stderr.write(line)
                    sys.stderr.flush()
                    log.write(line)
                    log.flush()
                    match = _EPOCH_PATTERN.search(line)
                    if match:
                        epoch = int(match.group(1))
                        if epoch != last_epoch:
                            last_epoch = epoch
                            on_progress(epoch, line.strip()[:500])
                            last_heartbeat = time.monotonic()
            if time.monotonic() - last_heartbeat >= 30:
                on_progress(last_epoch, "training heartbeat")
                last_heartbeat = time.monotonic()
        for line in process.stdout:
            sys.stderr.write(line)
            sys.stderr.flush()
            log.write(line)
            log.flush()
        selector.close()
        process.stdout.close()
        return_code = process.wait()
    if return_code != 0:
        raise DetectorTrainingError(f"PaddleOCR detector training failed with exit code {return_code}")


def _validate_completed_result(
    *,
    result_path: Path,
    state: Mapping[str, object],
    profile: Mapping[str, object],
) -> dict[str, object]:
    expected_result_sha = _require_sha256(state.get("result_sha256"), "completed detector result SHA-256")
    if not result_path.is_file() or _sha256_file(result_path) != expected_result_sha:
        raise DetectorTrainingError("completed detector training result SHA-256 mismatch")
    result = _json_object(result_path, "completed detector training result")
    if result.get("status") != "ok" or result.get("profile") != profile:
        raise DetectorTrainingError("completed detector training state/result profile disagree")
    best_checkpoint = Path(str(result.get("best_checkpoint") or ""))
    best_sha = result.get("best_checkpoint_sha256")
    _verify_file(best_checkpoint, best_sha, "completed detector best checkpoint")
    trained_config = Path(str(result.get("trained_config") or ""))
    _verify_file(trained_config, result.get("trained_config_sha256"), "completed detector trained config")
    if result.get("promotion_status") != "pending_project_safety_evaluation":
        raise DetectorTrainingError("completed detector training result has invalid promotion status")
    return result


def run_detector_training(
    *,
    upstream_path: str | Path,
    paddleocr_root: str | Path,
    pretrained_model: str | Path,
    corpus_manifest: str | Path,
    detection_export: str | Path,
    run_dir: str | Path,
    config: DetectorTrainingConfig,
) -> dict[str, object]:
    upstream_path = Path(upstream_path).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    pretrained_model = Path(pretrained_model).resolve()
    corpus_manifest = Path(corpus_manifest).resolve()
    detection_export = Path(detection_export).resolve()
    run_dir = Path(run_dir).resolve()
    ready = prepare_detector_training(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        corpus_manifest=corpus_manifest,
        detection_export=detection_export,
        run_dir=run_dir,
        config=config,
    )
    profile = ready["profile"]
    inputs = _load_inputs(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        corpus_manifest=corpus_manifest,
        detection_export=detection_export,
        config=config,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "training-state.json"
    result_path = run_dir / "result.json"
    model_dir = run_dir / "model"

    with _exclusive_lock(run_dir / ".detector-training.lock"):
        if result_path.is_file():
            state = _json_object(state_path, "detector training state")
            if state.get("profile") != profile:
                raise DetectorTrainingError("completed detector training profile differs from requested profile")
            if state.get("status") != "completed":
                raise DetectorTrainingError("detector training result exists without completed authoritative state")
            return _validate_completed_result(result_path=result_path, state=state, profile=profile)

        if state_path.is_file():
            state = _json_object(state_path, "detector training state")
            if state.get("profile") != profile:
                raise DetectorTrainingError("detector training state profile differs from requested profile")
            if state.get("status") == "completed":
                raise DetectorTrainingError("completed detector training state is missing its result")
            if state.get("status") not in {"running", "failed", "initializing"}:
                raise DetectorTrainingError("detector training state has unsupported status")
        else:
            unexpected = [path.name for path in run_dir.iterdir() if path.name != ".detector-training.lock"]
            if unexpected:
                raise DetectorTrainingError("detector training run directory is non-empty without authoritative state")
            state = {
                "schema_version": 1,
                "status": "initializing",
                "profile": profile,
                "created_at": _now(),
                "heartbeat_at": _now(),
                "current_epoch": 0,
            }
            _write_json_atomic(state_path, state)

        model_dir.mkdir(parents=True, exist_ok=True)
        resume = _find_resume_checkpoint(model_dir, maximum_epoch=config.epochs)
        resume_epoch = resume[0] if resume else 0
        resume_prefix = resume[1] if resume else None
        overrides = _training_overrides(
            data_dir=inputs["data_dir"],
            train_labels=inputs["labels"]["train"],
            val_labels=inputs["labels"]["val"],
            pretrained_model=pretrained_model,
            model_dir=model_dir,
            training_transforms=inputs["training_transforms"],
            config=config,
            resume_checkpoint=resume_prefix,
        )
        command = _training_command(
            paddleocr_root=paddleocr_root,
            document_config=inputs["document_config"],
            overrides=overrides,
        )
        if any(str(inputs["labels"]["test"]) in item for item in command):
            raise DetectorTrainingError("test labels must never appear in detector optimization command")

        state.update({
            "status": "running",
            "heartbeat_at": _now(),
            "current_epoch": resume_epoch,
            "resume_checkpoint": str(resume_prefix) if resume_prefix else None,
            "command": command,
        })
        _write_json_atomic(state_path, state)

        def on_progress(epoch: int, detail: str) -> None:
            previous_epoch = int(state.get("current_epoch") or 0)
            state["heartbeat_at"] = _now()
            state["current_epoch"] = max(previous_epoch, epoch)
            state["last_progress"] = detail
            _write_json_atomic(state_path, state)
            if epoch > previous_epoch:
                print(f"[ocr-det-train] epoch {epoch}/{config.epochs}", file=sys.stderr, flush=True)
            elif detail == "training heartbeat":
                print(
                    f"[ocr-det-train] heartbeat epoch={state['current_epoch']}/{config.epochs}",
                    file=sys.stderr,
                    flush=True,
                )

        try:
            _stream_training(
                command,
                cwd=paddleocr_root,
                log_path=run_dir / "train.log",
                on_progress=on_progress,
            )
            final_prefix = _checkpoint_prefix(model_dir, config.epochs)
            if not _checkpoint_complete(final_prefix):
                raise DetectorTrainingError(
                    f"detector training completed without a complete epoch {config.epochs} checkpoint"
                )
            best_checkpoint = model_dir / "best_accuracy.pdparams"
            if not best_checkpoint.is_file():
                raise DetectorTrainingError("detector training completed without best_accuracy.pdparams")
            trained_config = model_dir / "config.yml"
            if not trained_config.is_file():
                raise DetectorTrainingError("detector training completed without model/config.yml")
            result = {
                "schema_version": 1,
                "status": "ok",
                "profile": profile,
                "best_checkpoint": str(best_checkpoint),
                "best_checkpoint_sha256": _sha256_file(best_checkpoint),
                "trained_config": str(trained_config),
                "trained_config_sha256": _sha256_file(trained_config),
                "final_epoch_checkpoint": str(final_prefix),
                "promotion_status": "pending_project_safety_evaluation",
            }
            _write_json_atomic(result_path, result)
            state.update({
                "status": "completed",
                "heartbeat_at": _now(),
                "current_epoch": config.epochs,
                "result_sha256": _sha256_file(result_path),
            })
            _write_json_atomic(state_path, state)
            return result
        except Exception as exc:
            recovered = _find_resume_checkpoint(model_dir, maximum_epoch=config.epochs)
            state.update({
                "status": "failed",
                "heartbeat_at": _now(),
                "error": str(exc),
                "recoverable_checkpoint": str(recovered[1]) if recovered else None,
                "current_epoch": recovered[0] if recovered else int(state.get("current_epoch") or 0),
            })
            _write_json_atomic(state_path, state)
            raise


__all__ = [
    "DetectorTrainingConfig",
    "DetectorTrainingError",
    "prepare_detector_training",
    "run_detector_training",
]