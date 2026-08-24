from __future__ import annotations

import fcntl
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

from .training_contract import (
    _json_object,
    _now,
    _require_sha256,
    _sha256_file,
    _verify_file,
    _write_json_atomic,
)


class DetectorExportError(RuntimeError):
    pass


_SUPPORTED_TRAINING_RUNNERS = {
    "ppocrv5-mobile-document-detector-finetune-v1",
    "ppocrv5-mobile-document-detector-finetune-v2",
}
_PADDLE_FILES = ("inference.json", "inference.pdiparams", "inference.yml")


def _verify_training_result(path: Path) -> dict[str, object]:
    result = _json_object(path, "detector training result")
    if result.get("schema_version") != 1 or result.get("status") != "ok":
        raise DetectorExportError("detector training result is not a completed schema-v1 result")
    if result.get("promotion_status") != "pending_project_safety_evaluation":
        raise DetectorExportError("detector training result has invalid promotion status")
    profile = result.get("profile")
    if not isinstance(profile, Mapping) or profile.get("runner") not in _SUPPORTED_TRAINING_RUNNERS:
        raise DetectorExportError("detector training result has unsupported runner profile")
    commit = str(profile.get("paddleocr_commit") or "")
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise DetectorExportError("detector training result PaddleOCR commit is invalid")
    corpus = profile.get("corpus")
    if not isinstance(corpus, Mapping) or not str(corpus.get("id") or ""):
        raise DetectorExportError("detector training result is missing corpus identity")
    _require_sha256(corpus.get("manifest_sha256"), "detector training corpus manifest SHA-256")

    checkpoint = Path(str(result.get("best_checkpoint") or "")).resolve()
    if checkpoint.suffix != ".pdparams":
        raise DetectorExportError("detector best checkpoint must be a .pdparams file")
    checkpoint_sha = str(result.get("best_checkpoint_sha256") or "")
    try:
        _verify_file(checkpoint, checkpoint_sha, "detector best checkpoint")
    except Exception as exc:
        raise DetectorExportError(str(exc)) from exc

    trained_config = Path(str(result.get("trained_config") or "")).resolve()
    trained_config_sha = str(result.get("trained_config_sha256") or "")
    try:
        _verify_file(trained_config, trained_config_sha, "detector trained config")
    except Exception as exc:
        raise DetectorExportError(str(exc)) from exc
    return {
        "result": result,
        "profile": profile,
        "checkpoint": checkpoint,
        "checkpoint_sha256": checkpoint_sha,
        "trained_config": trained_config,
        "trained_config_sha256": trained_config_sha,
    }


def _export_command(
    *,
    paddleocr_root: Path,
    trained_config: Path,
    checkpoint: Path,
    paddle_dir: Path,
) -> list[str]:
    export_script = paddleocr_root / "tools/export_model.py"
    if not export_script.is_file():
        raise DetectorExportError(f"PaddleOCR export script does not exist: {export_script}")
    checkpoint_prefix = checkpoint.with_suffix("")
    return [
        sys.executable,
        "tools/export_model.py",
        "-c",
        str(trained_config),
        "-o",
        "Global.use_gpu=False",
        f"Global.checkpoints={checkpoint_prefix}",
        f"Global.save_inference_dir={paddle_dir}",
    ]


def prepare_paddle_export(
    *,
    training_result: str | Path,
    paddleocr_root: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    training_result = Path(training_result).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    output_dir = Path(output_dir).resolve()
    verified = _verify_training_result(training_result)
    profile = verified["profile"]
    corpus = profile["corpus"]
    export_profile = {
        "schema_version": 1,
        "runner": "ppocrv5-mobile-detector-paddle-export-v1",
        "training_runner": profile["runner"],
        "training_result_sha256": _sha256_file(training_result),
        "checkpoint_sha256": verified["checkpoint_sha256"],
        "trained_config_sha256": verified["trained_config_sha256"],
        "paddleocr_commit": profile["paddleocr_commit"],
        "corpus_id": corpus["id"],
        "corpus_manifest_sha256": corpus["manifest_sha256"],
    }
    command = _export_command(
        paddleocr_root=paddleocr_root,
        trained_config=verified["trained_config"],
        checkpoint=verified["checkpoint"],
        paddle_dir=output_dir / "paddle",
    )
    return {
        "schema_version": 1,
        "status": "ready",
        "profile": export_profile,
        "command": command,
        "output_dir": str(output_dir),
    }


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DetectorExportError(f"detector Paddle export is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _stream_export(command: list[str], *, cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
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
            sys.stderr.write(line)
            sys.stderr.flush()
            log.write(line)
            log.flush()
        process.stdout.close()
        code = process.wait()
    if code != 0:
        raise DetectorExportError(f"PaddleOCR detector export failed with exit code {code}")


def _bundle_files(paddle_dir: Path) -> dict[str, dict[str, object]]:
    files: dict[str, dict[str, object]] = {}
    for name in _PADDLE_FILES:
        path = paddle_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise DetectorExportError(f"Paddle detector export is missing {name}")
        files[name] = {"sha256": _sha256_file(path), "size_bytes": path.stat().st_size}
    return files


def _validate_completed(
    *,
    manifest_path: Path,
    state: Mapping[str, object],
    profile: Mapping[str, object],
    paddle_dir: Path,
) -> dict[str, object]:
    expected = _require_sha256(state.get("result_sha256"), "detector Paddle export result SHA-256")
    if not manifest_path.is_file() or _sha256_file(manifest_path) != expected:
        raise DetectorExportError("completed detector Paddle export result SHA-256 mismatch")
    manifest = _json_object(manifest_path, "detector Paddle export manifest")
    if manifest.get("status") != "ok" or manifest.get("profile") != profile:
        raise DetectorExportError("completed detector Paddle export profile mismatch")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != set(_PADDLE_FILES):
        raise DetectorExportError("completed detector Paddle export file manifest is invalid")
    for name in _PADDLE_FILES:
        metadata = files[name]
        if not isinstance(metadata, Mapping):
            raise DetectorExportError(f"completed detector Paddle export metadata is invalid for {name}")
        try:
            _verify_file(
                paddle_dir / name,
                metadata.get("sha256"),
                f"completed detector Paddle export {name}",
                expected_bytes=metadata.get("size_bytes"),
            )
        except Exception as exc:
            raise DetectorExportError(str(exc)) from exc
    return manifest


def run_paddle_export(
    *,
    training_result: str | Path,
    paddleocr_root: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    training_result = Path(training_result).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    output_dir = Path(output_dir).resolve()
    ready = prepare_paddle_export(
        training_result=training_result,
        paddleocr_root=paddleocr_root,
        output_dir=output_dir,
    )
    profile = ready["profile"]
    paddle_dir = output_dir / "paddle"
    state_path = output_dir / "stage-state.json"
    manifest_path = output_dir / "stage-manifest.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    with _exclusive_lock(output_dir / ".paddle-export.lock"):
        if manifest_path.is_file():
            state = _json_object(state_path, "detector Paddle export state")
            if state.get("status") != "completed" or state.get("profile") != profile:
                raise DetectorExportError("detector Paddle export result exists without matching completed state")
            return _validate_completed(
                manifest_path=manifest_path,
                state=state,
                profile=profile,
                paddle_dir=paddle_dir,
            )

        if state_path.is_file():
            state = _json_object(state_path, "detector Paddle export state")
            if state.get("profile") != profile:
                raise DetectorExportError("detector Paddle export state profile differs from requested profile")
            if state.get("status") not in {"initializing", "running", "failed"}:
                raise DetectorExportError("detector Paddle export state has unsupported status")
        else:
            unexpected = [path.name for path in output_dir.iterdir() if path.name != ".paddle-export.lock"]
            if unexpected:
                raise DetectorExportError("detector Paddle export directory is non-empty without authoritative state")
            state = {
                "schema_version": 1,
                "status": "initializing",
                "profile": profile,
                "created_at": _now(),
            }
            _write_json_atomic(state_path, state)

        shutil.rmtree(paddle_dir, ignore_errors=True)
        state.update({"status": "running", "started_at": _now(), "command": ready["command"]})
        _write_json_atomic(state_path, state)
        try:
            _stream_export(
                ready["command"],
                cwd=paddleocr_root,
                log_path=output_dir / "paddle-export.log",
            )
            files = _bundle_files(paddle_dir)
            manifest = {
                "schema_version": 1,
                "status": "ok",
                "profile": profile,
                "source_training_result": str(training_result),
                "paddle_directory": str(paddle_dir),
                "files": files,
                "promotion_status": "pending_onnx_conversion_and_safety_evaluation",
            }
            _write_json_atomic(manifest_path, manifest)
            state.update(
                {
                    "status": "completed",
                    "completed_at": _now(),
                    "result_sha256": _sha256_file(manifest_path),
                }
            )
            _write_json_atomic(state_path, state)
            return manifest
        except Exception as exc:
            state.update({"status": "failed", "failed_at": _now(), "error": str(exc)})
            _write_json_atomic(state_path, state)
            raise


__all__ = [
    "DetectorExportError",
    "prepare_paddle_export",
    "run_paddle_export",
]