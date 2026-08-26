from __future__ import annotations

import fcntl
import math
import re
import selectors
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .dataset import DatasetError, load_dataset
from .model_compat import audit_model_compatibility
from .recognizer_training_contract import (
    build_recognizer_training_overrides,
    validate_recognizer_training_view,
)
from .runner_io import json_file, sha256_file, verify_sha, write_json_atomic
from .training import export_identity, find_resume_checkpoint, format_paddle_override
from .training_view import TRAINING_VIEW_POLICY_ID


@dataclass(frozen=True)
class V6RecognizerTrainingConfig:
    epochs: int = 4
    batch_size: int = 32
    learning_rate: float = 0.00005
    warmup_epochs: int = 1
    num_workers: int = 2

    def validate(self) -> None:
        if isinstance(self.epochs, bool) or not isinstance(self.epochs, int) or self.epochs <= 0:
            raise DatasetError("epochs must be a positive integer")
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int) or self.batch_size <= 0:
            raise DatasetError("batch size must be a positive integer")
        if not isinstance(self.learning_rate, (int, float)) or isinstance(self.learning_rate, bool):
            raise DatasetError("learning rate must be numeric")
        if not math.isfinite(float(self.learning_rate)) or float(self.learning_rate) <= 0:
            raise DatasetError("learning rate must be positive and finite")
        if (
            isinstance(self.warmup_epochs, bool)
            or not isinstance(self.warmup_epochs, int)
            or self.warmup_epochs < 0
            or self.warmup_epochs >= self.epochs
        ):
            raise DatasetError("warmup epochs must be non-negative and less than total epochs")
        if isinstance(self.num_workers, bool) or not isinstance(self.num_workers, int) or self.num_workers <= 0:
            raise DatasetError("num workers must be a positive integer")


ProgressCallback = Callable[[int, str], None]
_EPOCH_PATTERN = re.compile(r"epoch:\s*\[(\d+)/(\d+)\]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DatasetError(f"{label} must be an object")
    return value


def _validate_upstream(
    *,
    upstream_path: Path,
    paddleocr_root: Path,
    pretrained_model: Path,
) -> dict[str, object]:
    upstream = json_file(upstream_path)
    if upstream.get("schema_version") != 1 or upstream.get("framework") != "PaddleOCR":
        raise DatasetError("recognizer upstream contract is unsupported")
    if upstream.get("recognizer") != "korean_PP-OCRv5_mobile_rec":
        raise DatasetError("recognizer upstream contract does not select korean_PP-OCRv5_mobile_rec")
    if upstream.get("pin_status") != "training-smoke-verified" or upstream.get("training_enabled") is not True:
        raise DatasetError("recognizer training runtime has not passed the pinned smoke gate")

    paddle = _require_mapping(upstream.get("paddleocr"), "recognizer PaddleOCR contract")
    commit = str(paddle.get("commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DatasetError("recognizer PaddleOCR commit is invalid")
    config_value = paddle.get("config_path")
    dictionary_value = paddle.get("dictionary_path")
    if not isinstance(config_value, str) or not config_value:
        raise DatasetError("recognizer PaddleOCR config path is missing")
    if not isinstance(dictionary_value, str) or not dictionary_value:
        raise DatasetError("recognizer PaddleOCR dictionary path is missing")
    config_path = (paddleocr_root / config_value).resolve()
    dictionary_path = (paddleocr_root / dictionary_value).resolve()
    for path, label in ((config_path, "recognizer config"), (dictionary_path, "recognizer dictionary")):
        try:
            path.relative_to(paddleocr_root)
        except ValueError as exc:
            raise DatasetError(f"{label} path escapes PaddleOCR root") from exc
    verify_sha(config_path, str(paddle.get("config_sha256") or ""), "PaddleOCR recognizer config")
    verify_sha(dictionary_path, str(paddle.get("dictionary_sha256") or ""), "PaddleOCR recognizer dictionary")

    expected_bytes = upstream.get("pretrained_model_bytes")
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
        raise DatasetError("recognizer pretrained model byte count is invalid")
    if not pretrained_model.is_file() or pretrained_model.stat().st_size != expected_bytes:
        raise DatasetError("recognizer pretrained model byte count does not match upstream pin")
    verify_sha(
        pretrained_model,
        str(upstream.get("pretrained_model_sha256") or ""),
        "recognizer pretrained model",
    )
    contract = _require_mapping(upstream.get("model_contract"), "recognizer model contract")
    max_text_length = contract.get("max_text_length")
    use_space_char = contract.get("use_space_char")
    if isinstance(max_text_length, bool) or not isinstance(max_text_length, int) or max_text_length <= 0:
        raise DatasetError("recognizer max_text_length is invalid")
    if not isinstance(use_space_char, bool):
        raise DatasetError("recognizer use_space_char must be boolean")
    train_script = paddleocr_root / "tools" / "train.py"
    if not train_script.is_file():
        raise DatasetError(f"PaddleOCR training script does not exist: {train_script}")
    return {
        "upstream": upstream,
        "commit": commit,
        "config_path": config_path,
        "dictionary_path": dictionary_path,
        "max_text_length": max_text_length,
        "use_space_char": use_space_char,
    }


def _load_inputs(
    *,
    upstream_path: Path,
    paddleocr_root: Path,
    pretrained_model: Path,
    manifest: Path,
    export_dir: Path,
    config: V6RecognizerTrainingConfig,
) -> dict[str, object]:
    config.validate()
    upstream = _validate_upstream(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
    )
    dataset = load_dataset(manifest)
    policy = dataset.manifest.get("metadata", {}).get("training_view_policy")
    if not isinstance(policy, Mapping) or policy.get("policy_id") != TRAINING_VIEW_POLICY_ID:
        raise DatasetError("v6 recognizer training requires the unified recognition training-view policy")
    compatibility = audit_model_compatibility(
        dataset,
        upstream["dictionary_path"],
        max_text_length=int(upstream["max_text_length"]),
        use_space_char=bool(upstream["use_space_char"]),
    )
    if compatibility.get("status") != "ok":
        raise DatasetError("v6 recognizer dataset is incompatible with the pinned recognizer contract")
    view = validate_recognizer_training_view(
        dataset,
        export_dir,
        expected_dictionary_sha256=str(upstream["upstream"]["paddleocr"]["dictionary_sha256"]),
        expected_max_text_length=int(upstream["max_text_length"]),
        expected_use_space_char=bool(upstream["use_space_char"]),
    )
    counts = view.get("counts")
    if not isinstance(counts, dict) or any(not isinstance(counts.get(name), int) or counts[name] <= 0 for name in ("train", "val", "test")):
        raise DatasetError("v6 recognizer training view split counts are invalid")
    return {
        **upstream,
        "dataset": dataset,
        "policy": policy,
        "view": view,
        "counts": counts,
    }


def _profile(
    *,
    upstream_path: Path,
    pretrained_model: Path,
    manifest: Path,
    export_dir: Path,
    inputs: Mapping[str, object],
    config: V6RecognizerTrainingConfig,
) -> dict[str, object]:
    dataset = inputs["dataset"]
    return {
        "schema_version": 1,
        "runner": "ppocrv5-korean-mobile-unified-recognizer-finetune-v1",
        "upstream_sha256": sha256_file(upstream_path),
        "paddleocr_commit": inputs["commit"],
        "config_sha256": sha256_file(inputs["config_path"]),
        "dictionary_sha256": sha256_file(inputs["dictionary_path"]),
        "pretrained_model_sha256": sha256_file(pretrained_model),
        "dataset": {
            "id": dataset.manifest["dataset_id"],
            "fingerprint": dataset.fingerprint,
            "manifest_sha256": sha256_file(manifest),
            "training_view_profile_sha256": inputs["policy"].get("profile_sha256"),
            "counts": inputs["counts"],
            "export_identity": export_identity(export_dir),
        },
        "hyperparameters": asdict(config),
        "optimization_splits": ["train", "val"],
        "promotion_evaluation_split": "test",
    }


def _training_command(
    *,
    paddleocr_root: Path,
    config_path: Path,
    pretrained_model: Path,
    dataset_root: Path,
    export_dir: Path,
    model_dir: Path,
    config: V6RecognizerTrainingConfig,
    resume_checkpoint: Path | None,
) -> list[str]:
    overrides = build_recognizer_training_overrides(
        dataset_root=dataset_root,
        export_dir=export_dir,
        initial_checkpoint=pretrained_model,
        resume_checkpoint=resume_checkpoint,
        output_dir=model_dir,
        batch_size=config.batch_size,
        epochs=config.epochs,
        learning_rate=config.learning_rate,
        warmup_epochs=config.warmup_epochs,
    )
    overrides["Train.loader.num_workers"] = config.num_workers
    overrides["Eval.loader.num_workers"] = config.num_workers
    command = [sys.executable, "tools/train.py", "-c", str(config_path), "-o"]
    command.extend(f"{key}={format_paddle_override(value)}" for key, value in overrides.items())
    if any(str(export_dir / "test.txt") in value for value in command):
        raise DatasetError("test labels must never appear in recognizer optimization command")
    if not (paddleocr_root / "tools" / "train.py").is_file():
        raise DatasetError("PaddleOCR recognizer training script disappeared after preflight")
    return command


def prepare_v6_recognizer_training(
    *,
    upstream_path: str | Path,
    paddleocr_root: str | Path,
    pretrained_model: str | Path,
    manifest: str | Path,
    export_dir: str | Path,
    run_dir: str | Path,
    config: V6RecognizerTrainingConfig,
) -> dict[str, object]:
    upstream_path = Path(upstream_path).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    pretrained_model = Path(pretrained_model).resolve()
    manifest = Path(manifest).resolve()
    export_dir = Path(export_dir).resolve()
    run_dir = Path(run_dir).resolve()
    inputs = _load_inputs(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        manifest=manifest,
        export_dir=export_dir,
        config=config,
    )
    profile = _profile(
        upstream_path=upstream_path,
        pretrained_model=pretrained_model,
        manifest=manifest,
        export_dir=export_dir,
        inputs=inputs,
        config=config,
    )
    command = _training_command(
        paddleocr_root=paddleocr_root,
        config_path=inputs["config_path"],
        pretrained_model=pretrained_model,
        dataset_root=inputs["dataset"].root,
        export_dir=export_dir,
        model_dir=run_dir / "model",
        config=config,
        resume_checkpoint=None,
    )
    return {
        "schema_version": 1,
        "status": "ready",
        "profile": profile,
        "command": command,
        "run_dir": str(run_dir),
        "promotion": "requires_project_safety_evaluation",
    }


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"v6 recognizer training is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


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
        code = process.wait()
    if code != 0:
        raise DatasetError(f"PaddleOCR recognizer training failed with exit code {code}")


def _checkpoint_complete(prefix: Path) -> bool:
    return all(Path(str(prefix) + suffix).is_file() for suffix in (".pdparams", ".pdopt", ".states"))


def _validate_completed_result(
    *,
    result_path: Path,
    state: Mapping[str, object],
    profile: Mapping[str, object],
) -> dict[str, object]:
    expected_sha = str(state.get("result_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or sha256_file(result_path) != expected_sha:
        raise DatasetError("completed v6 recognizer result SHA-256 mismatch")
    result = json_file(result_path)
    if result.get("status") != "ok" or result.get("profile") != profile:
        raise DatasetError("completed v6 recognizer state/result profile disagree")
    verify_sha(Path(result["best_checkpoint"]), result["best_checkpoint_sha256"], "v6 recognizer best checkpoint")
    verify_sha(Path(result["trained_config"]), result["trained_config_sha256"], "v6 recognizer trained config")
    if result.get("promotion_status") != "pending_project_safety_evaluation":
        raise DatasetError("completed v6 recognizer result has invalid promotion status")
    return result


def run_v6_recognizer_training(
    *,
    upstream_path: str | Path,
    paddleocr_root: str | Path,
    pretrained_model: str | Path,
    manifest: str | Path,
    export_dir: str | Path,
    run_dir: str | Path,
    config: V6RecognizerTrainingConfig,
) -> dict[str, object]:
    upstream_path = Path(upstream_path).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    pretrained_model = Path(pretrained_model).resolve()
    manifest = Path(manifest).resolve()
    export_dir = Path(export_dir).resolve()
    run_dir = Path(run_dir).resolve()
    ready = prepare_v6_recognizer_training(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        manifest=manifest,
        export_dir=export_dir,
        run_dir=run_dir,
        config=config,
    )
    profile = ready["profile"]
    inputs = _load_inputs(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        manifest=manifest,
        export_dir=export_dir,
        config=config,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "training-state.json"
    result_path = run_dir / "result.json"
    model_dir = run_dir / "model"

    with _exclusive_lock(run_dir / ".recognizer-training.lock"):
        if result_path.is_file():
            if not state_path.is_file():
                raise DatasetError("v6 recognizer result exists without authoritative state")
            state = json_file(state_path)
            if state.get("status") != "completed" or state.get("profile") != profile:
                raise DatasetError("v6 recognizer completed state does not match requested profile")
            return _validate_completed_result(result_path=result_path, state=state, profile=profile)

        if state_path.is_file():
            state = json_file(state_path)
            if state.get("profile") != profile:
                raise DatasetError("v6 recognizer state profile differs from requested profile")
            if state.get("status") == "completed":
                raise DatasetError("completed v6 recognizer state is missing result")
            if state.get("status") not in {"initializing", "running", "failed"}:
                raise DatasetError("v6 recognizer state has unsupported status")
        else:
            unexpected = [path.name for path in run_dir.iterdir() if path.name != ".recognizer-training.lock"]
            if unexpected:
                raise DatasetError("v6 recognizer run directory is non-empty without authoritative state")
            state = {
                "schema_version": 1,
                "status": "initializing",
                "profile": profile,
                "created_at": _now(),
                "heartbeat_at": _now(),
                "current_epoch": 0,
            }
            write_json_atomic(state_path, state)

        model_dir.mkdir(parents=True, exist_ok=True)
        resume_prefix = find_resume_checkpoint(model_dir)
        if resume_prefix is None and state.get("status") in {"failed", "running"}:
            # A failed process may leave partial Paddle files that do not form a
            # recoverable epoch checkpoint. Never mix them into a clean retry.
            for child in model_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        resume_epoch = 0
        if resume_prefix is not None:
            match = re.fullmatch(r"iter_epoch_(\d+)", resume_prefix.name)
            if match is None:
                raise DatasetError("v6 recognizer resume checkpoint name is invalid")
            resume_epoch = int(match.group(1))
            if resume_epoch > config.epochs:
                raise DatasetError("v6 recognizer resume checkpoint exceeds requested epochs")
        command = _training_command(
            paddleocr_root=paddleocr_root,
            config_path=inputs["config_path"],
            pretrained_model=pretrained_model,
            dataset_root=inputs["dataset"].root,
            export_dir=export_dir,
            model_dir=model_dir,
            config=config,
            resume_checkpoint=resume_prefix,
        )
        state.update(
            {
                "status": "running",
                "heartbeat_at": _now(),
                "current_epoch": resume_epoch,
                "resume_checkpoint": str(resume_prefix) if resume_prefix else None,
                "command": command,
            }
        )
        write_json_atomic(state_path, state)

        def on_progress(epoch: int, detail: str) -> None:
            previous = int(state.get("current_epoch") or 0)
            state["heartbeat_at"] = _now()
            state["current_epoch"] = max(previous, epoch)
            state["last_progress"] = detail
            write_json_atomic(state_path, state)
            if epoch > previous:
                print(f"[ocr-rec-train] epoch {epoch}/{config.epochs}", file=sys.stderr, flush=True)
            elif detail == "training heartbeat":
                print(
                    f"[ocr-rec-train] heartbeat epoch={state['current_epoch']}/{config.epochs}",
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
            final_prefix = model_dir / f"iter_epoch_{config.epochs}"
            if not _checkpoint_complete(final_prefix):
                raise DatasetError(f"v6 recognizer training completed without epoch {config.epochs} checkpoint")
            best_checkpoint = model_dir / "best_accuracy.pdparams"
            if not best_checkpoint.is_file():
                raise DatasetError("v6 recognizer training completed without best_accuracy.pdparams")
            trained_config = model_dir / "config.yml"
            if not trained_config.is_file():
                raise DatasetError("v6 recognizer training completed without model/config.yml")
            result = {
                "schema_version": 1,
                "status": "ok",
                "profile": profile,
                "best_checkpoint": str(best_checkpoint),
                "best_checkpoint_sha256": sha256_file(best_checkpoint),
                "trained_config": str(trained_config),
                "trained_config_sha256": sha256_file(trained_config),
                "final_epoch_checkpoint": str(final_prefix) + ".pdparams",
                "final_epoch_checkpoint_sha256": sha256_file(Path(str(final_prefix) + ".pdparams")),
                "promotion_status": "pending_project_safety_evaluation",
            }
            write_json_atomic(result_path, result)
            state.update(
                {
                    "status": "completed",
                    "heartbeat_at": _now(),
                    "current_epoch": config.epochs,
                    "result_sha256": sha256_file(result_path),
                }
            )
            write_json_atomic(state_path, state)
            return result
        except Exception as exc:
            recovered = find_resume_checkpoint(model_dir)
            recovered_epoch = 0
            if recovered is not None:
                match = re.fullmatch(r"iter_epoch_(\d+)", recovered.name)
                recovered_epoch = int(match.group(1)) if match else 0
            state.update(
                {
                    "status": "failed",
                    "heartbeat_at": _now(),
                    "error": str(exc),
                    "recoverable_checkpoint": str(recovered) if recovered else None,
                    "current_epoch": recovered_epoch,
                }
            )
            write_json_atomic(state_path, state)
            raise


__all__ = [
    "V6RecognizerTrainingConfig",
    "prepare_v6_recognizer_training",
    "run_v6_recognizer_training",
]