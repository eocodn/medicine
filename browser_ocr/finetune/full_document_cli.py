from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .dataset import DatasetError
from .runtime_environment import runtime_environment_sha256 as _runtime_environment_sha256


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
        "full_document_runtime": browser_root / "finetune" / "full_document_runtime.py",
        "recognizer_runtime": browser_root / "finetune" / "recognizer_runtime.py",
        "crop_refinement": browser_root / "finetune" / "crop_refinement.py",
        "orientation": browser_root / "finetune" / "orientation.py",
        "orientation_runtime": browser_root / "finetune" / "orientation_runtime.py",
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


def _selected_dictionary(config_path: Path, paddleocr_root: Path) -> Path:
    configured: str | None = None
    in_global = False
    global_indent = -1
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        content = raw_line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        stripped = content.strip()
        if stripped.startswith("Global:"):
            in_global = True
            global_indent = indent
            continue
        if in_global and indent <= global_indent:
            in_global = False
        if in_global and stripped.startswith("character_dict_path:"):
            raw_value = stripped.split(":", 1)[1].strip()
            if not raw_value:
                raise DatasetError("selected recognizer character_dict_path is empty")
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
                raw_value = raw_value[1:-1]
            configured = raw_value
            break
    if configured:
        dictionary = Path(configured)
        if not dictionary.is_absolute():
            dictionary = paddleocr_root / dictionary
    else:
        dictionary = paddleocr_root / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt"
    dictionary = dictionary.resolve()
    if not dictionary.is_file():
        raise DatasetError(f"selected recognizer dictionary is missing: {dictionary}")
    return dictionary


def _paddleocr_profile(root: Path, config_path: Path) -> dict[str, str]:
    infer_script = root / "tools" / "infer_rec.py"
    tools_root = root / "tools"
    package_root = root / "ppocr"
    if not infer_script.is_file() or not tools_root.is_dir() or not package_root.is_dir():
        raise DatasetError(f"PaddleOCR inference source is incomplete: {root}")
    dictionary = _selected_dictionary(config_path, root)
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
    paddleocr = _paddleocr_profile(Path(args.paddleocr_root).resolve(), Path(selected["config"]).resolve())
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
        "inference_runtime_sha256": _runtime_environment_sha256(args.recognizer_device),
        "paddleocr_source_sha256": paddleocr["source_sha256"],
        "paddleocr_dictionary_sha256": paddleocr["dictionary_sha256"],
        "implementation": implementation,
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


def run_full_document(args: argparse.Namespace) -> dict[str, object]:
    from .full_document_runtime import FullDocumentRuntime

    runtime = FullDocumentRuntime(args)
    return runtime.run(image_path=args.image, output_dir=args.output_dir)


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
