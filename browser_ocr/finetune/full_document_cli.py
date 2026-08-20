from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata as importlib_metadata
import platform
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from browser_ocr.document_parsing.baseline import BASELINE_ID

from .crop_refinement import refine_prediction_crops
from .dataset import DatasetError
from .full_document import (
    build_document_regions,
    parse_document_regions,
    parse_recognition_rows,
    recognition_quality,
    sort_text_predictions,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _implementation_profile() -> dict[str, str]:
    browser_root = Path(__file__).resolve().parents[1]
    sources = {
        "full_document": browser_root / "finetune" / "full_document.py",
        "full_document_cli": Path(__file__).resolve(),
        "crop_refinement": browser_root / "finetune" / "crop_refinement.py",
        "parser": browser_root / "document_parsing" / "baseline.py",
        "parser_contract": browser_root / "document_parsing" / "contract.py",
        "detector_runtime": browser_root / "detection" / "runtime.py",
        "detector_benchmark": browser_root / "detection" / "detector_benchmark.py",
    }
    return {name: _sha256_file(path) for name, path in sources.items()}


def _read_json_object(path: Path, label: str) -> dict:
    if not path.is_file():
        raise DatasetError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must be a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_selected_recognizer(baseline_result_path: str | Path) -> dict[str, object]:
    result_path = Path(baseline_result_path).resolve()
    result = _read_json_object(result_path, "baseline result")
    if result.get("status") != "ok":
        raise DatasetError("baseline result is not completed successfully")
    checkpoint_value = result.get("best_checkpoint")
    expected_sha = result.get("best_checkpoint_sha256")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        raise DatasetError("baseline result is missing best_checkpoint")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise DatasetError("baseline result is missing best_checkpoint_sha256")
    checkpoint = Path(checkpoint_value).resolve()
    if not checkpoint.is_file():
        raise DatasetError(f"selected recognizer checkpoint does not exist: {checkpoint}")
    actual_sha = _sha256_file(checkpoint)
    if actual_sha != expected_sha:
        raise DatasetError(f"selected recognizer SHA-256 mismatch: {checkpoint}")
    config = checkpoint.parent / "config.yml"
    if not config.is_file():
        raise DatasetError(f"selected recognizer config does not exist: {config}")
    return {
        "result_path": result_path,
        "result": result,
        "checkpoint": checkpoint,
        "checkpoint_sha256": actual_sha,
        "config": config,
        "config_sha256": _sha256_file(config),
    }


def _detector_profile(manifest_path: Path, model_root: Path, model_name: str) -> dict[str, object]:
    manifest = _read_json_object(manifest_path, "detector model manifest")
    model = manifest.get("models", {}).get(model_name)
    if not isinstance(model, dict):
        raise DatasetError(f"unknown detector model: {model_name}")
    asset_sha = model.get("sha256")
    if not isinstance(asset_sha, str) or len(asset_sha) != 64:
        raise DatasetError(f"detector model {model_name} is missing a pinned archive SHA-256")
    archive_root = model.get("archive_root")
    onnx_file = model.get("onnx_file")
    config_file = model.get("config_file")
    if not all(isinstance(value, str) and value for value in (archive_root, onnx_file, config_file)):
        raise DatasetError(f"detector model {model_name} is missing extracted asset paths")
    extracted = model_root / archive_root
    onnx_path = extracted / onnx_file
    config_path = extracted / config_file
    if not onnx_path.is_file() or not config_path.is_file():
        raise DatasetError(f"detector model {model_name} extracted assets are incomplete")
    return {
        "asset_sha256": asset_sha,
        "manifest_sha256": _sha256_file(manifest_path),
        "onnx_sha256": _sha256_file(onnx_path),
        "config_sha256": _sha256_file(config_path),
    }


def _runtime_environment_sha256() -> str:
    distributions = sorted({
        (str(dist.metadata.get("Name") or "").lower(), str(dist.version))
        for dist in importlib_metadata.distributions()
        if dist.metadata.get("Name")
    })
    finetune_root = Path(__file__).resolve().parent
    runtime_contract = {
        name: _sha256_file(finetune_root / name)
        for name in ("Dockerfile.train", "requirements-train.lock", "requirements-paddle-runtime.lock")
    }
    payload = {
        "python": sys.version,
        "python_implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "system": platform.system(),
        "distributions": distributions,
        "runtime_contract": runtime_contract,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _paddleocr_profile(root: Path) -> dict[str, str]:
    infer_script = root / "tools" / "infer_rec.py"
    tools_root = root / "tools"
    package_root = root / "ppocr"
    dictionary = package_root / "utils" / "dict" / "ppocrv5_korean_dict.txt"
    if not infer_script.is_file() or not tools_root.is_dir() or not package_root.is_dir():
        raise DatasetError(f"PaddleOCR inference source is incomplete: {root}")
    if not dictionary.is_file():
        raise DatasetError(f"PaddleOCR Korean dictionary is missing: {dictionary}")
    source_files = sorted({*tools_root.rglob("*.py"), *package_root.rglob("*.py")})
    digest = hashlib.sha256()
    for path in source_files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return {
        "source_sha256": digest.hexdigest(),
        "dictionary_sha256": _sha256_file(dictionary),
    }


def build_ocr_producer_profile(
    args: argparse.Namespace,
    recognizer: dict[str, object] | None = None,
) -> dict[str, object]:
    selected = recognizer or load_selected_recognizer(args.baseline_result)
    manifest_path = Path(args.detector_manifest).resolve()
    detector = _detector_profile(manifest_path, Path(args.detector_root).resolve(), args.detector_model)
    if args.detector_edge <= 0:
        raise DatasetError("detector edge must be positive")
    if args.detector_threads <= 0:
        raise DatasetError("detector threads must be positive")
    implementation = _implementation_profile()
    paddleocr = _paddleocr_profile(Path(args.paddleocr_root).resolve())
    return {
        "schema_version": 2,
        "baseline_result_sha256": _sha256_file(selected["result_path"]),
        "recognizer_checkpoint_sha256": selected["checkpoint_sha256"],
        "recognizer_config_sha256": selected["config_sha256"],
        "recognizer_device": args.recognizer_device,
        "detector_manifest_sha256": detector["manifest_sha256"],
        "detector_model": args.detector_model,
        "detector_edge": args.detector_edge,
        "detector_threads": args.detector_threads,
        "detector_asset_sha256": detector["asset_sha256"],
        "detector_onnx_sha256": detector["onnx_sha256"],
        "detector_config_sha256": detector["config_sha256"],
        "inference_runtime_sha256": _runtime_environment_sha256(),
        "paddleocr_source_sha256": paddleocr["source_sha256"],
        "paddleocr_dictionary_sha256": paddleocr["dictionary_sha256"],
        "implementation": {
            key: value
            for key, value in implementation.items()
            if key not in {"parser", "parser_contract"}
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-full-document")
    parser.add_argument("--image", required=True)
    parser.add_argument("--baseline-result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    parser.add_argument("--detector-manifest", default="/workspace/browser_ocr/detection/detector-models.json")
    parser.add_argument("--detector-root", default="/workspace/browser_ocr/detection/.cache/models")
    parser.add_argument("--detector-model", default="PP-OCRv5_mobile_det")
    parser.add_argument("--detector-edge", type=int, default=640)
    parser.add_argument("--detector-threads", type=int, default=1)
    parser.add_argument("--recognizer-device", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--json", action="store_true")
    return parser


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"full-document OCR is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _run_logged(command: list[str], *, cwd: Path, log_path: Path) -> None:
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
        return_code = process.wait()
    if return_code != 0:
        raise DatasetError(f"recognizer inference failed with exit code {return_code}")


def _profile(args: argparse.Namespace, image: Path, recognizer: dict[str, object]) -> dict[str, object]:
    producer = build_ocr_producer_profile(args, recognizer)
    implementation = _implementation_profile()
    return {
        **producer,
        "image_sha256": _sha256_file(image),
        "parser": BASELINE_ID,
        "implementation": {
            **producer["implementation"],
            "parser": implementation["parser"],
            "parser_contract": implementation["parser_contract"],
        },
    }


def _recognize_crops(
    *,
    paddleocr_root: Path,
    config_path: Path,
    checkpoint: Path,
    crop_dir: Path,
    output_path: Path,
    log_path: Path,
    use_gpu: bool,
) -> dict[str, dict[str, object]]:
    command = [
        sys.executable,
        "tools/infer_rec.py",
        "-c",
        str(config_path),
        "-o",
        f"Global.use_gpu={'True' if use_gpu else 'False'}",
        "Global.distributed=False",
        f"Global.checkpoints={checkpoint}",
        f"Global.infer_img={crop_dir}",
        f"Global.save_res_path={output_path}",
    ]
    _run_logged(command, cwd=paddleocr_root, log_path=log_path)
    expected = [str(path.resolve()) for path in sorted(crop_dir.glob("region-*.png"))]
    if not output_path.is_file():
        raise DatasetError("recognizer inference did not produce its result file")
    return parse_recognition_rows(output_path.read_text(encoding="utf-8"), expected)


def run_full_document(args: argparse.Namespace) -> dict[str, object]:
    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        raise DatasetError(f"input image does not exist: {image_path}")
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    result_path = output_dir / "result.json"
    recognizer = load_selected_recognizer(args.baseline_result)
    profile = _profile(args, image_path, recognizer)

    with _exclusive_lock(output_dir / ".pipeline.lock"):
        if state_path.is_file():
            state = _read_json_object(state_path, "full-document state")
            if state.get("profile") != profile:
                raise DatasetError("full-document output profile differs from the requested inputs/models")
            if state.get("status") == "completed":
                expected_result_sha = state.get("result_sha256")
                if not isinstance(expected_result_sha, str) or len(expected_result_sha) != 64:
                    raise DatasetError("completed full-document state is missing result SHA-256")
                if not result_path.is_file() or _sha256_file(result_path) != expected_result_sha:
                    raise DatasetError("completed full-document result SHA-256 mismatch")
                result = _read_json_object(result_path, "full-document result")
                if result.get("profile") != profile or result.get("status") != "ok":
                    raise DatasetError("completed full-document state/result disagree")
                return result
        elif any(path.name != ".pipeline.lock" for path in output_dir.iterdir()):
            raise DatasetError("full-document output directory is non-empty without authoritative state")

        state = {"schema_version": 2, "status": "running", "profile": profile}
        _write_json_atomic(state_path, state)
        crop_dir = output_dir / "crops"
        shutil.rmtree(crop_dir, ignore_errors=True)
        crop_dir.mkdir(parents=True)
        for stale in (output_dir / "recognition.txt", output_dir / "recognition.log", result_path):
            stale.unlink(missing_ok=True)

        try:
            import cv2

            from browser_ocr.detection.runtime import load_detector_runtime

            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise DatasetError(f"failed to decode input image: {image_path}")
            detector_started = time.perf_counter()
            detector = load_detector_runtime(
                model_manifest_path=Path(args.detector_manifest),
                model_root=Path(args.detector_root),
                model_name=args.detector_model,
                detector_edge=args.detector_edge,
                threads=args.detector_threads,
            )
            if detector.onnx_sha256 != profile["detector_onnx_sha256"] or detector.config_sha256 != profile["detector_config_sha256"]:
                raise DatasetError("loaded detector assets changed after OCR producer profile resolution")
            predictions = sort_text_predictions(detector.predict(image))
            prediction_crops = refine_prediction_crops(image, predictions)
            predictions = [prediction for prediction, _ in prediction_crops]
            detector_ms = (time.perf_counter() - detector_started) * 1000.0

            crop_paths: list[str] = []
            for index, (_, crop) in enumerate(prediction_crops, start=1):
                crop_path = crop_dir / f"region-{index:04d}.png"
                if not cv2.imwrite(str(crop_path), crop):
                    raise DatasetError(f"failed to write recognition crop: {crop_path}")
                crop_paths.append(str(crop_path.resolve()))

            recognition_ms = 0.0
            if crop_paths:
                recognition_started = time.perf_counter()
                recognized = _recognize_crops(
                    paddleocr_root=Path(args.paddleocr_root).resolve(),
                    config_path=recognizer["config"],
                    checkpoint=recognizer["checkpoint"],
                    crop_dir=crop_dir,
                    output_path=output_dir / "recognition.txt",
                    log_path=output_dir / "recognition.log",
                    use_gpu=args.recognizer_device == "gpu",
                )
                recognition_ms = (time.perf_counter() - recognition_started) * 1000.0
                regions = build_document_regions(predictions, crop_paths, recognized)
                recognition_status = "ok"
            else:
                regions = []
                recognition_status = "skipped_no_detections"

            quality = recognition_quality(regions)
            parser_input_regions = sum(bool(str(region.get("text") or "").strip()) for region in regions)
            parsing_started = time.perf_counter()
            if not regions:
                medications = []
                parsing_status = "skipped_no_regions"
            elif not quality["safe_for_structured_parsing"]:
                # A document-level confidence collapse is not recoverable by
                # geometry rules. Abstain rather than emitting exact medication
                # values from a recognizer that is broadly signaling uncertainty.
                medications = []
                parsing_status = "abstained_low_ocr_quality"
            else:
                medications = parse_document_regions(regions)
                parsing_status = "ok"
            parsing_ms = (time.perf_counter() - parsing_started) * 1000.0

            height, width = image.shape[:2]
            result = {
                "schema_version": 2,
                "status": "ok",
                "profile": profile,
                "image": {
                    "path": str(image_path),
                    "sha256": profile["image_sha256"],
                    "width": width,
                    "height": height,
                },
                "stages": {
                    "detection": {
                        "status": "ok",
                        "model": detector.model_name,
                        "detector_edge": detector.detector_edge,
                        "boxes": len(predictions),
                        "latency_ms": round(detector_ms, 3),
                        "model_bytes": detector.model_bytes,
                    },
                    "recognition": {
                        "status": recognition_status,
                        "model": "korean_PP-OCRv5_mobile_rec",
                        "checkpoint_sha256": recognizer["checkpoint_sha256"],
                        "regions": len(regions),
                        "latency_ms": round(recognition_ms, 3),
                        "device": args.recognizer_device,
                    },
                    "parsing": {
                        "status": parsing_status,
                        "parser": BASELINE_ID,
                        "input_regions": parser_input_regions,
                        "skipped_empty_text_regions": len(regions) - parser_input_regions,
                        "recognition_quality": quality,
                        "rows": len(medications),
                        "latency_ms": round(parsing_ms, 3),
                    },
                },
                "regions": regions,
                "medications": medications,
                "text_lines": [region["text"] for region in regions],
            }
            _write_json_atomic(result_path, result)
            _write_json_atomic(state_path, {
                "schema_version": 2,
                "status": "completed",
                "profile": profile,
                "result_sha256": _sha256_file(result_path),
            })
            return result
        except Exception as exc:
            _write_json_atomic(
                state_path,
                {"schema_version": 2, "status": "failed", "profile": profile, "error": str(exc)},
            )
            raise


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_full_document(args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (DatasetError, ValueError, KeyError, OSError, RuntimeError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
