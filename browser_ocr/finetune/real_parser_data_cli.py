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
    REAL_PARSER_LOCK_FILE,
)
from browser_ocr.document_parsing.observation_profile import runtime_observation_producer
from browser_ocr.document_parsing.training_dataset import ParserDatasetError

from .full_document_cli import build_ocr_producer_profile
from .full_document_runtime import FullDocumentRuntime


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
    parser.add_argument("--recognizer-result", required=True)
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
    recognizer_result = Path(args.recognizer_result).resolve()
    if not source_manifest.is_file():
        raise ParserDatasetError(f"real source manifest does not exist: {source_manifest}")
    if not recognizer_result.is_file():
        raise ParserDatasetError(f"recognizer result does not exist: {recognizer_result}")
    return {
        "schema_version": 2,
        "source_dataset_id": source.dataset_id,
        "source_manifest_sha256": _sha256_file(source_manifest),
        "source_samples_sha256": _sha256_file(source.samples_path),
        "ocr_producer": build_ocr_producer_profile(args),
    }


def _validate_completed_artifacts(
    *,
    source,
    profile: Mapping[str, Any],
    runtime_root: Path,
    annotations_root: Path,
) -> None:
    index_path = annotations_root / "index.json"
    if not index_path.is_file():
        raise ParserDatasetError("completed real parser batch is missing annotation index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, Mapping) or index.get("schema_version") != 3:
        raise ParserDatasetError("completed real parser annotation index is invalid")
    if index.get("source_dataset_id") != source.dataset_id:
        raise ParserDatasetError("completed annotation index source dataset disagrees with batch")
    if index.get("source_manifest") != str(source.manifest_path) or index.get("source_manifest_sha256") != profile["source_manifest_sha256"]:
        raise ParserDatasetError("completed annotation index source manifest disagrees with batch")
    if index.get("source_samples") != str(source.samples_path) or index.get("source_samples_sha256") != profile["source_samples_sha256"]:
        raise ParserDatasetError("completed annotation index source samples disagree with batch")
    if index.get("ocr_producer") != profile["ocr_producer"]:
        raise ParserDatasetError("completed annotation index OCR producer disagrees with batch")
    entries = index.get("documents")
    if not isinstance(entries, list) or len(entries) != len(source.samples):
        raise ParserDatasetError("completed annotation index document set is incomplete")
    entries_by_id: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "document_id", "annotation", "immutable_sha256", "runtime_result", "runtime_result_sha256"
        }:
            raise ParserDatasetError("completed annotation index entry is invalid")
        document_id = str(entry["document_id"])
        if document_id in entries_by_id:
            raise ParserDatasetError("completed annotation index contains duplicate document ids")
        entries_by_id[document_id] = entry
    for sample in source.samples:
        document_id = str(sample["document_id"])
        entry = entries_by_id.get(document_id)
        if entry is None:
            raise ParserDatasetError("completed annotation index is missing a source document")
        annotation_relative = Path(str(entry["annotation"] or ""))
        if annotation_relative.is_absolute() or ".." in annotation_relative.parts:
            raise ParserDatasetError("completed annotation index annotation path is invalid")
        annotation_path = annotations_root / annotation_relative
        expected_runtime_path = runtime_root / document_id / "result.json"
        if str(entry["runtime_result"] or "") != str(expected_runtime_path):
            raise ParserDatasetError("completed annotation index runtime result path disagrees with batch")
        if not annotation_path.is_file() or not expected_runtime_path.is_file():
            raise ParserDatasetError("completed real parser batch is missing annotation/runtime artifacts")
        if _sha256_file(expected_runtime_path) != str(entry["runtime_result_sha256"] or ""):
            raise ParserDatasetError("completed runtime OCR result SHA-256 mismatch")
        runtime_result = json.loads(expected_runtime_path.read_text(encoding="utf-8"))
        if not isinstance(runtime_result, Mapping):
            raise ParserDatasetError("completed runtime OCR result is invalid")
        if runtime_observation_producer(runtime_result.get("profile"), expected_image_sha256=str(sample["image_sha256"])) != profile["ocr_producer"]:
            raise ParserDatasetError("completed runtime OCR producer differs from real batch profile")
        annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        if not isinstance(annotation, Mapping) or str(annotation.get("document_id") or "") != document_id:
            raise ParserDatasetError("completed real annotation document id is invalid")
        expected = prepare_real_annotation(sample, runtime_result)
        expected_immutable = annotation_immutable_sha256(expected)
        if str(entry["immutable_sha256"] or "") != expected_immutable or annotation_immutable_sha256(annotation) != expected_immutable:
            raise ParserDatasetError("completed real annotation immutable snapshot differs from runtime OCR")


def run_real_batch(args: argparse.Namespace) -> dict[str, Any]:
    source = load_real_source_manifest(args.source_manifest)
    output = Path(args.output_dir).resolve()
    runtime_root = output / "runtime"
    annotations_root = output / "annotations"
    output.mkdir(parents=True, exist_ok=True)
    profile = _batch_profile(args, source)
    state_path = output / "state.json"
    with _exclusive_lock(output / REAL_PARSER_LOCK_FILE):
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
                if result.get("documents") != len(source.samples):
                    raise ParserDatasetError("completed real parser batch result document count disagrees with state")
                if result.get("runtime_root") != str(runtime_root) or result.get("annotations_dir") != str(annotations_root):
                    raise ParserDatasetError("completed real parser batch result paths disagree with output")
                _validate_completed_artifacts(
                    source=source,
                    profile=profile,
                    runtime_root=runtime_root,
                    annotations_root=annotations_root,
                )
                return dict(result)
            if state.get("status") != "running":
                raise ParserDatasetError("real parser batch state has unsupported status")
        else:
            completed = 0
            unexpected = [path.name for path in output.iterdir() if path.name != REAL_PARSER_LOCK_FILE]
            if unexpected:
                raise ParserDatasetError("real parser batch output is non-empty without authoritative state")
            _atomic_json(state_path, {"schema_version": 1, "status": "running", "profile": profile, "completed": 0})

        entries: list[dict[str, str]] = []
        runtime: FullDocumentRuntime | None = None
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
                if runtime_observation_producer(result.get("profile"), expected_image_sha256=str(sample["image_sha256"])) != profile["ocr_producer"]:
                    raise ParserDatasetError("completed runtime OCR producer differs from real batch profile")
                expected = prepare_real_annotation(sample, result)
                if annotation_immutable_sha256(annotation) != annotation_immutable_sha256(expected):
                    raise ParserDatasetError("completed real annotation immutable snapshot differs from runtime OCR")
            else:
                if annotation_path.is_file():
                    if not result_path.is_file():
                        raise ParserDatasetError("uncheckpointed real annotation is missing its runtime OCR result")
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if runtime_observation_producer(result.get("profile"), expected_image_sha256=str(sample["image_sha256"])) != profile["ocr_producer"]:
                        raise ParserDatasetError("uncheckpointed runtime OCR producer differs from real batch profile")
                    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
                    expected = prepare_real_annotation(sample, result)
                    if annotation_immutable_sha256(annotation) != annotation_immutable_sha256(expected):
                        raise ParserDatasetError("uncheckpointed real annotation immutable snapshot differs from runtime OCR")
                else:
                    if runtime is None:
                        runtime = FullDocumentRuntime(args)
                    result = runtime.run(
                        image_path=image_path,
                        output_dir=runtime_root / document_id,
                    )
                    if runtime_observation_producer(result.get("profile"), expected_image_sha256=str(sample["image_sha256"])) != profile["ocr_producer"]:
                        raise ParserDatasetError("runtime OCR producer differs from real batch profile")
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
            "schema_version": 3,
            "source_dataset_id": source.dataset_id,
            "source_manifest": str(source.manifest_path),
            "source_manifest_sha256": profile["source_manifest_sha256"],
            "source_samples": str(source.samples_path),
            "source_samples_sha256": profile["source_samples_sha256"],
            "ocr_producer": profile["ocr_producer"],
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
