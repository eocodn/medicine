from __future__ import annotations

import fcntl
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

from .runtime_spec import load_detector_runtime_spec
from .training_contract import _json_object, _now, _require_sha256, _sha256_file, _verify_file, _write_json_atomic


class CandidateConversionError(RuntimeError):
    pass


PADDLE_VERSION = "3.2.0"
PADDLE2ONNX_VERSION = "2.1.0"
ONNX_VERSION = "1.17.0"
ONNXRUNTIME_VERSION = "1.23.2"
ONNX_OPSET = 17
PARITY_MAX_ABS_ERROR = 1e-4
_STAGE_RUNNER = "ppocrv5-mobile-detector-paddle-export-v1"
_PADDLE_FILES = ("inference.json", "inference.pdiparams", "inference.yml")


def _verify_stage(stage_manifest: Path) -> dict[str, object]:
    stage = _json_object(stage_manifest, "detector Paddle export manifest")
    if stage.get("schema_version") != 1 or stage.get("status") != "ok":
        raise CandidateConversionError("detector Paddle export manifest is not completed")
    if stage.get("promotion_status") != "pending_onnx_conversion_and_safety_evaluation":
        raise CandidateConversionError("detector Paddle export manifest has invalid promotion status")
    profile = stage.get("profile")
    if not isinstance(profile, Mapping) or profile.get("runner") != _STAGE_RUNNER:
        raise CandidateConversionError("detector Paddle export manifest has unsupported runner")
    for key in (
        "training_result_sha256",
        "checkpoint_sha256",
        "trained_config_sha256",
        "corpus_manifest_sha256",
    ):
        try:
            _require_sha256(profile.get(key), f"detector Paddle export {key}")
        except Exception as exc:
            raise CandidateConversionError(str(exc)) from exc
    if not str(profile.get("corpus_id") or ""):
        raise CandidateConversionError("detector Paddle export manifest is missing corpus id")

    state_path = stage_manifest.parent / "stage-state.json"
    state = _json_object(state_path, "detector Paddle export state")
    if state.get("status") != "completed" or state.get("profile") != profile:
        raise CandidateConversionError("detector Paddle export state does not match completed manifest")
    expected_manifest_sha = _require_sha256(
        state.get("result_sha256"),
        "detector Paddle export state result SHA-256",
    )
    actual_manifest_sha = _sha256_file(stage_manifest)
    if actual_manifest_sha != expected_manifest_sha:
        raise CandidateConversionError("detector Paddle export manifest SHA-256 does not match authoritative state")

    paddle_dir = Path(str(stage.get("paddle_directory") or "")).resolve()
    expected_dir = (stage_manifest.parent / "paddle").resolve()
    if paddle_dir != expected_dir:
        raise CandidateConversionError("detector Paddle export directory is not bound to its stage directory")
    files = stage.get("files")
    if not isinstance(files, Mapping) or set(files) != set(_PADDLE_FILES):
        raise CandidateConversionError("detector Paddle export file manifest is invalid")
    for name in _PADDLE_FILES:
        metadata = files[name]
        if not isinstance(metadata, Mapping):
            raise CandidateConversionError(f"detector Paddle export metadata is invalid for {name}")
        try:
            _verify_file(
                paddle_dir / name,
                metadata.get("sha256"),
                f"detector Paddle export {name}",
                expected_bytes=metadata.get("size_bytes"),
            )
        except Exception as exc:
            raise CandidateConversionError(str(exc)) from exc
    return {
        "stage": stage,
        "profile": profile,
        "paddle_dir": paddle_dir,
        "stage_manifest_sha256": actual_manifest_sha,
    }


def _conversion_command(*, paddle_dir: Path, output_dir: Path) -> list[str]:
    return [
        "paddle2onnx",
        "--model_dir",
        str(paddle_dir),
        "--model_filename",
        "inference.json",
        "--params_filename",
        "inference.pdiparams",
        "--save_file",
        str(output_dir / "inference.onnx"),
        "--opset_version",
        str(ONNX_OPSET),
        "--enable_auto_update_opset",
        "True",
        "--enable_onnx_checker",
        "True",
        "--optimize_tool",
        "None",
    ]


def prepare_candidate_conversion(
    *,
    stage_manifest: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    stage_manifest = Path(stage_manifest).resolve()
    output_dir = Path(output_dir).resolve()
    verified = _verify_stage(stage_manifest)
    stage_profile = verified["profile"]
    profile = {
        "schema_version": 1,
        "runner": "ppocrv5-mobile-detector-onnx-convert-v1",
        "stage_manifest_sha256": verified["stage_manifest_sha256"],
        "training_result_sha256": stage_profile["training_result_sha256"],
        "checkpoint_sha256": stage_profile["checkpoint_sha256"],
        "paddleocr_commit": stage_profile["paddleocr_commit"],
        "corpus_id": stage_profile["corpus_id"],
        "corpus_manifest_sha256": stage_profile["corpus_manifest_sha256"],
        "onnx_opset": ONNX_OPSET,
    }
    return {
        "schema_version": 1,
        "status": "ready",
        "profile": profile,
        "command": _conversion_command(paddle_dir=verified["paddle_dir"], output_dir=output_dir),
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
            raise CandidateConversionError(f"detector candidate conversion is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _run_converter(command: list[str], *, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        process.stdout.close()
        code = process.wait()
    if code != 0:
        raise CandidateConversionError(f"paddle2onnx conversion failed with exit code {code}")


def _toolchain() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "paddle": importlib.metadata.version("paddlepaddle"),
        "paddle2onnx": importlib.metadata.version("paddle2onnx"),
        "onnx": importlib.metadata.version("onnx"),
        "onnxruntime": importlib.metadata.version("onnxruntime"),
    }
    expected = {
        "paddle": PADDLE_VERSION,
        "paddle2onnx": PADDLE2ONNX_VERSION,
        "onnx": ONNX_VERSION,
        "onnxruntime": ONNXRUNTIME_VERSION,
    }
    for name, version in expected.items():
        if versions[name] != version:
            raise CandidateConversionError(
                f"detector converter {name} version mismatch: expected {version}, got {versions[name]}"
            )
    if not versions["python"].startswith("3.10."):
        raise CandidateConversionError(f"detector converter requires Python 3.10, got {versions['python']}")
    return versions


def _deterministic_tensor(shape: tuple[int, int, int, int]):
    import numpy as np

    count = int(np.prod(shape))
    values = np.arange(count, dtype=np.float32)
    values = ((values % 257.0) / 128.0) - 1.0
    return values.reshape(shape)


def _paddle_outputs(program: Path, params: Path, tensor) -> list[object]:
    import paddle.inference as paddle_infer

    config = paddle_infer.Config(str(program), str(params))
    config.disable_gpu()
    predictor = paddle_infer.create_predictor(config)
    input_names = predictor.get_input_names()
    if len(input_names) != 1:
        raise CandidateConversionError(f"detector Paddle inference expected one input, got {input_names}")
    input_handle = predictor.get_input_handle(input_names[0])
    input_handle.reshape(tensor.shape)
    input_handle.copy_from_cpu(tensor)
    predictor.run()
    return [predictor.get_output_handle(name).copy_to_cpu() for name in predictor.get_output_names()]


def _verify_onnx_and_parity(*, paddle_dir: Path, onnx_path: Path) -> dict[str, object]:
    import numpy as np
    import onnx
    import onnxruntime as ort

    toolchain = _toolchain()
    onnx.checker.check_model(str(onnx_path))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise CandidateConversionError(f"detector ONNX expected one input, got {len(inputs)}")
    input_name = inputs[0].name
    output_names = [item.name for item in session.get_outputs()]
    if not output_names:
        raise CandidateConversionError("detector ONNX has no outputs")

    checks = []
    maximum = 0.0
    for shape in ((1, 3, 320, 320), (1, 3, 384, 512)):
        tensor = _deterministic_tensor(shape)
        paddle_outputs = _paddle_outputs(
            paddle_dir / "inference.json",
            paddle_dir / "inference.pdiparams",
            tensor,
        )
        onnx_outputs = session.run(output_names, {input_name: tensor})
        if len(paddle_outputs) != len(onnx_outputs):
            raise CandidateConversionError("detector Paddle/ONNX output count mismatch")
        errors = []
        for paddle_output, onnx_output in zip(paddle_outputs, onnx_outputs, strict=True):
            if paddle_output.shape != onnx_output.shape:
                raise CandidateConversionError(
                    f"detector Paddle/ONNX output shape mismatch: {paddle_output.shape} vs {onnx_output.shape}"
                )
            error = float(np.max(np.abs(paddle_output.astype(np.float32) - onnx_output.astype(np.float32))))
            errors.append(error)
        shape_max = max(errors, default=0.0)
        maximum = max(maximum, shape_max)
        checks.append({"shape": list(shape), "max_abs_error": shape_max})
    if maximum > PARITY_MAX_ABS_ERROR:
        raise CandidateConversionError(
            f"detector Paddle/ONNX parity exceeds {PARITY_MAX_ABS_ERROR}: {maximum}"
        )
    return {"toolchain": toolchain, "checks": checks, "max_abs_error": maximum}


def _benchmark_manifest(*, key: str, onnx_sha: str, runtime_spec: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "medicine trained detector candidate",
        "models": {
            key: {
                "archive_root": ".",
                "onnx_file": "inference.onnx",
                "config_file": "inference.yml",
                "sha256": onnx_sha,
                "onnx_sha256": onnx_sha,
                "config_model_name": runtime_spec["model_name"],
                "preprocess": runtime_spec["preprocess"],
                "postprocess": runtime_spec["postprocess"],
            }
        },
    }


def _verify_completed(output_dir: Path, profile: Mapping[str, object]) -> dict[str, object]:
    state = _json_object(output_dir / "candidate-state.json", "detector candidate state")
    if state.get("status") != "completed" or state.get("profile") != profile:
        raise CandidateConversionError("detector candidate completed state/profile mismatch")
    expected = _require_sha256(state.get("result_sha256"), "detector candidate result SHA-256")
    result_path = output_dir / "candidate.json"
    if not result_path.is_file() or _sha256_file(result_path) != expected:
        raise CandidateConversionError("detector candidate result SHA-256 mismatch")
    result = _json_object(result_path, "detector candidate result")
    if result.get("status") != "ok" or result.get("profile") != profile:
        raise CandidateConversionError("detector candidate result/profile mismatch")
    try:
        _verify_file(output_dir / "inference.onnx", result.get("onnx_sha256"), "detector candidate ONNX")
        _verify_file(
            output_dir / "inference.yml",
            result.get("inference_config_sha256"),
            "detector candidate inference config",
        )
        _verify_file(
            output_dir / "benchmark-models.json",
            result.get("benchmark_manifest_sha256"),
            "detector candidate benchmark manifest",
        )
    except Exception as exc:
        raise CandidateConversionError(str(exc)) from exc
    return result


def run_candidate_conversion(
    *,
    stage_manifest: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    stage_manifest = Path(stage_manifest).resolve()
    output_dir = Path(output_dir).resolve()
    ready = prepare_candidate_conversion(stage_manifest=stage_manifest, output_dir=output_dir)
    profile = ready["profile"]
    verified = _verify_stage(stage_manifest)
    paddle_dir = verified["paddle_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "candidate-state.json"
    result_path = output_dir / "candidate.json"

    with _exclusive_lock(output_dir / ".candidate-convert.lock"):
        if result_path.is_file():
            return _verify_completed(output_dir, profile)
        if state_path.is_file():
            state = _json_object(state_path, "detector candidate state")
            if state.get("profile") != profile:
                raise CandidateConversionError("detector candidate state profile differs from requested profile")
            if state.get("status") not in {"initializing", "running", "failed"}:
                raise CandidateConversionError("detector candidate state has unsupported status")
        else:
            allowed = {".candidate-convert.lock"}
            unexpected = [path.name for path in output_dir.iterdir() if path.name not in allowed]
            if unexpected:
                raise CandidateConversionError("detector candidate directory is non-empty without authoritative state")
            state = {"schema_version": 1, "status": "initializing", "profile": profile, "created_at": _now()}
            _write_json_atomic(state_path, state)

        for name in ("inference.onnx", "inference.yml", "benchmark-models.json", "candidate.json"):
            (output_dir / name).unlink(missing_ok=True)
        state.update({"status": "running", "started_at": _now(), "command": ready["command"]})
        _write_json_atomic(state_path, state)
        try:
            _run_converter(ready["command"], log_path=output_dir / "conversion.log")
            onnx_path = output_dir / "inference.onnx"
            if not onnx_path.is_file() or onnx_path.stat().st_size <= 0:
                raise CandidateConversionError("paddle2onnx did not produce inference.onnx")
            parity = _verify_onnx_and_parity(paddle_dir=paddle_dir, onnx_path=onnx_path)
            shutil.copyfile(paddle_dir / "inference.yml", output_dir / "inference.yml")
            runtime_spec = load_detector_runtime_spec(output_dir / "inference.yml")
            onnx_sha = _sha256_file(onnx_path)
            model_key = f"PP-OCRv5_mobile_det_candidate_{str(profile['checkpoint_sha256'])[:12]}"
            benchmark = _benchmark_manifest(key=model_key, onnx_sha=onnx_sha, runtime_spec=runtime_spec)
            benchmark_path = output_dir / "benchmark-models.json"
            _write_json_atomic(benchmark_path, benchmark)
            result = {
                "schema_version": 1,
                "status": "ok",
                "profile": profile,
                "benchmark_model_key": model_key,
                "onnx_sha256": onnx_sha,
                "onnx_size_bytes": onnx_path.stat().st_size,
                "inference_config_sha256": _sha256_file(output_dir / "inference.yml"),
                "benchmark_manifest_sha256": _sha256_file(benchmark_path),
                "runtime_spec": runtime_spec,
                "parity": parity,
                "promotion_status": "pending_project_safety_evaluation",
            }
            _write_json_atomic(result_path, result)
            state.update({"status": "completed", "completed_at": _now(), "result_sha256": _sha256_file(result_path)})
            _write_json_atomic(state_path, state)
            return result
        except Exception as exc:
            state.update({"status": "failed", "failed_at": _now(), "error": str(exc)})
            _write_json_atomic(state_path, state)
            raise


__all__ = [
    "CandidateConversionError",
    "prepare_candidate_conversion",
    "run_candidate_conversion",
]