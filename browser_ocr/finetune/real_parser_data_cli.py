from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from browser_ocr.document_parsing.real_data import (
    annotation_immutable_sha256,
    load_real_source_manifest,
    prepare_real_annotation,
)
from browser_ocr.document_parsing.training_dataset import ParserDatasetError

from .full_document_cli import build_parser as build_full_document_parser
from .full_document_cli import run_full_document


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ParserDatasetError(f"real parser batch is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-real",
        description="Run the selected full-document OCR stack over external de-identified prescription photos and create parser annotation drafts",
    )
    parser.add_argument("--source-manifest", required=True)
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


def _batch_profile(args: argparse.Namespace, source) -> dict[str, Any]:
    source_manifest = Path(args.source_manifest).resolve()
    baseline = Path(args.baseline_result).resolve()
    if not source_manifest.is_file():
        raise ParserDatasetError(f"real source manifest does not exist: {source_manifest}")
    if not baseline.is_file():
        raise ParserDatasetError(f"baseline result does not exist: {baseline}")
    return {
        "schema_version": 1,
        "source_dataset_id": source.dataset_id,
        "source_manifest_sha256": _sha256_file(source_manifest),
        "source_samples_sha256": _sha256_file(source.samples_path),
        "baseline_result_sha256": _sha256_file(baseline),
        "detector_model": args.detector_model,
        "detector_edge": args.detector_edge,
        "detector_threads": args.detector_threads,
        "recognizer_device": args.recognizer_device,
    }


def _full_document_args(
    args: argparse.Namespace,
    *,
    image_path: Path,
    output_dir: Path,
) -> argparse.Namespace:
    return build_full_document_parser().parse_args(
        [
            "--image", str(image_path),
            "--baseline-result", str(Path(args.baseline_result).resolve()),
            "--output-dir", str(output_dir),
            "--paddleocr-root", str(Path(args.paddleocr_root).resolve()),
            "--detector-manifest", str(Path(args.detector_manifest).resolve()),
            "--detector-root", str(Path(args.detector_root).resolve()),
            "--detector-model", args.detector_model,
            "--detector-edge", str(args.detector_edge),
            "--detector-threads", str(args.detector_threads),
            "--recognizer-device", args.recognizer_device,
            "--json",
        ]
    )


def run_real_batch(args: argparse.Namespace) -> dict[str, Any]:
    source = load_real_source_manifest(args.source_manifest)
    output = Path(args.output_dir).resolve()
    runtime_root = output / "runtime"
    annotations_root = output / "annotations"
    output.mkdir(parents=True, exist_ok=True)
    profile = _batch_profile(args, source)
    state_path = output / "state.json"
    with _exclusive_lock(output / ".batch.lock"):
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ParserDatasetError("real parser batch state is invalid JSON") from exc
            if not isinstance(state, Mapping) or state.get("profile") != profile:
                raise ParserDatasetError("real parser batch output profile differs from requested inputs/models")
            completed = state.get("completed")
            if isinstance(completed, bool) or not isinstance(completed, int) or not 0 <= completed <= len(source.samples):
                raise ParserDatasetError("real parser batch completed counter is invalid")
            if state.get("status") == "completed":
                result_path = output / "result.json"
                if not result_path.is_file():
                    raise ParserDatasetError("completed real parser batch is missing result.json")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if not isinstance(result, Mapping) or result.get("status") != "ok" or result.get("profile") != profile:
                    raise ParserDatasetError("completed real parser batch state/result disagree")
                return dict(result)
            if state.get("status") != "running":
                raise ParserDatasetError("real parser batch state has unsupported status")
        else:
            completed = 0
            unexpected = [path.name for path in output.iterdir() if path.name != ".batch.lock"]
            if unexpected:
                raise ParserDatasetError("real parser batch output is non-empty without authoritative state")
            _atomic_json(state_path, {"schema_version": 1, "status": "running", "profile": profile, "completed": 0})

        entries: list[dict[str, str]] = []
        for index, sample in enumerate(source.samples, start=1):
            document_id = str(sample["document_id"])
            image_path = (source.root / str(sample["image"])).resolve()
            result_path = runtime_root / document_id / "result.json"
            annotation_path = annotations_root / f"{document_id}.json"
            if index <= completed:
                if not result_path.is_file() or not annotation_path.is_file():
                    raise ParserDatasetError("real parser batch checkpoint is missing completed artifacts")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
                expected = prepare_real_annotation(sample, result)
                if annotation_immutable_sha256(annotation) != annotation_immutable_sha256(expected):
                    raise ParserDatasetError("completed real annotation immutable snapshot differs from runtime OCR")
            else:
                if annotation_path.exists():
                    raise ParserDatasetError("uncheckpointed real annotation already exists; refusing to overwrite")
                result = run_full_document(
                    _full_document_args(
                        args,
                        image_path=image_path,
                        output_dir=runtime_root / document_id,
                    )
                )
                annotation = prepare_real_annotation(sample, result)
                _atomic_json(annotation_path, annotation)
                _atomic_json(
                    state_path,
                    {"schema_version": 1, "status": "running", "profile": profile, "completed": index},
                )
                print(f"[ocr-parser-real] {index}/{len(source.samples)} {document_id}", file=sys.stderr, flush=True)
            entries.append({
                "document_id": document_id,
                "annotation": annotation_path.name,
                "immutable_sha256": annotation_immutable_sha256(annotation),
                "runtime_result": str(result_path),
                "runtime_result_sha256": _sha256_file(result_path),
            })

        annotation_index = {
            "schema_version": 2,
            "source_dataset_id": source.dataset_id,
            "source_manifest": str(source.manifest_path),
            "source_manifest_sha256": profile["source_manifest_sha256"],
            "source_samples": str(source.samples_path),
            "source_samples_sha256": profile["source_samples_sha256"],
            "runtime_profile": profile,
            "documents": entries,
        }
        _atomic_json(annotations_root / "index.json", annotation_index)
        result = {
            "status": "ok",
            "documents": len(entries),
            "runtime_root": str(runtime_root),
            "annotations_dir": str(annotations_root),
            "profile": profile,
        }
        _atomic_json(output / "result.json", result)
        _atomic_json(
            state_path,
            {"schema_version": 1, "status": "completed", "profile": profile, "completed": len(entries)},
        )
        return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_real_batch(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=bool(args.json), indent=None if args.json else 2))
        return 0
    except (OSError, ParserDatasetError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
