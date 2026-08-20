from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .real_data import (
    annotation_immutable_sha256,
    finalize_real_annotation,
    load_real_source_manifest,
    prepare_real_annotation,
)
from .training_builders import build_runtime_dataset, build_synthetic_dataset
from .training_dataset import ParserDatasetError, load_parser_dataset, write_parser_dataset


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ParserDatasetError(f"parser data operation is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _emit(value: object, json_output: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=json_output, indent=None if json_output else 2))


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ParserDatasetError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParserDatasetError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ParserDatasetError(f"{label} must be an object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-parser-data", description="Parser training/validation dataset controls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--allow-draft", action="store_true")
    validate.add_argument("--json", action="store_true")

    synthetic = subparsers.add_parser("build-synthetic")
    synthetic.add_argument("--truth-samples", required=True)
    synthetic.add_argument("--output-dir", required=True)
    synthetic.add_argument("--dataset-id", required=True)
    synthetic.add_argument("--observation-kind", choices=("oracle", "synthetic_ocr"), required=True)
    synthetic.add_argument("--split", choices=("train", "val", "test"))
    synthetic.add_argument("--seed", type=int, default=112)
    synthetic.add_argument("--json", action="store_true")

    runtime = subparsers.add_parser("build-runtime")
    runtime.add_argument("--truth-samples", required=True)
    runtime.add_argument("--results-root", required=True)
    runtime.add_argument("--output-dir", required=True)
    runtime.add_argument("--dataset-id", required=True)
    runtime.add_argument("--split", choices=("train", "val", "test"))
    runtime.add_argument("--json", action="store_true")

    prepare = subparsers.add_parser("prepare-real")
    prepare.add_argument("--source-manifest", required=True)
    prepare.add_argument("--results-root", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--json", action="store_true")

    finalize = subparsers.add_parser("finalize-real")
    finalize.add_argument("--annotations-dir", required=True)
    finalize.add_argument("--dataset-id", required=True)
    finalize.add_argument("--output-dir", required=True)
    finalize.add_argument("--json", action="store_true")
    return parser


def _prepare_real(source_manifest: str, results_root: str, output_dir: str) -> dict[str, Any]:
    source = load_real_source_manifest(source_manifest)
    root = Path(results_root).resolve()
    output = Path(output_dir).resolve()
    annotations = output / "annotations"
    annotations.mkdir(parents=True, exist_ok=True)
    source_manifest_sha = _sha256_file(source.manifest_path)
    source_samples_sha = _sha256_file(source.samples_path)
    index_path = annotations / "index.json"
    with _exclusive_lock(output / ".prepare-real.lock"):
        if index_path.is_file():
            index = _read_json(index_path, "annotation index")
            if index.get("source_manifest_sha256") != source_manifest_sha:
                raise ParserDatasetError("existing real annotation index source manifest differs")
            if index.get("source_samples_sha256") != source_samples_sha:
                raise ParserDatasetError("existing real annotation index source samples differ")
            entries = index.get("documents")
            if not isinstance(entries, list) or len(entries) != len(source.samples):
                raise ParserDatasetError("existing real annotation index is incomplete")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ParserDatasetError("existing real annotation index entry is invalid")
                annotation_path = annotations / str(entry.get("annotation") or "")
                runtime_path = Path(str(entry.get("runtime_result") or ""))
                if not annotation_path.is_file() or not runtime_path.is_file():
                    raise ParserDatasetError("existing real annotation inputs are incomplete")
                if _sha256_file(runtime_path) != entry.get("runtime_result_sha256"):
                    raise ParserDatasetError("existing runtime OCR result changed after annotation preparation")
                annotation = _read_json(annotation_path, "real annotation")
                if annotation_immutable_sha256(annotation) != entry.get("immutable_sha256"):
                    raise ParserDatasetError("existing real annotation immutable snapshot changed")
            return {"status": "ok", "documents": len(entries), "annotations_dir": str(annotations), "reused": True}

        unexpected = [path.name for path in annotations.iterdir() if path.name != "index.json"]
        if unexpected:
            raise ParserDatasetError("real annotation output is non-empty without authoritative index")
        entries: list[dict[str, str]] = []
        for index, sample in enumerate(source.samples, start=1):
            result_path = root / str(sample["document_id"]) / "result.json"
            result = _read_json(result_path, "runtime OCR result")
            annotation = prepare_real_annotation(sample, result)
            annotation_path = annotations / f"{sample['document_id']}.json"
            _atomic_json(annotation_path, annotation)
            entries.append({
                "document_id": str(sample["document_id"]),
                "annotation": annotation_path.name,
                "immutable_sha256": annotation_immutable_sha256(annotation),
                "runtime_result": str(result_path),
                "runtime_result_sha256": _sha256_file(result_path),
            })
            print(f"[ocr-parser-data] real annotation {index}/{len(source.samples)}", file=sys.stderr, flush=True)
        index_value = {
            "schema_version": 2,
            "source_dataset_id": source.dataset_id,
            "source_manifest": str(source.manifest_path),
            "source_manifest_sha256": source_manifest_sha,
            "source_samples": str(source.samples_path),
            "source_samples_sha256": source_samples_sha,
            "documents": entries,
        }
        _atomic_json(index_path, index_value)
        return {"status": "ok", "documents": len(entries), "annotations_dir": str(annotations), "reused": False}


def _finalize_real(annotations_dir: str, dataset_id: str, output_dir: str) -> dict[str, Any]:
    root = Path(annotations_dir).resolve()
    index = _read_json(root / "index.json", "annotation index")
    if index.get("schema_version") != 2:
        raise ParserDatasetError("annotation index schema_version must be 2")
    source_manifest = Path(str(index.get("source_manifest") or ""))
    if not source_manifest.is_file():
        raise ParserDatasetError("annotation index source manifest does not exist")
    if _sha256_file(source_manifest) != str(index.get("source_manifest_sha256") or ""):
        raise ParserDatasetError("annotation index source manifest SHA-256 mismatch")
    source_samples = Path(str(index.get("source_samples") or ""))
    if not source_samples.is_file() or _sha256_file(source_samples) != str(index.get("source_samples_sha256") or ""):
        raise ParserDatasetError("annotation index source samples SHA-256 mismatch")
    source = load_real_source_manifest(source_manifest)
    source_by_id = {str(sample["document_id"]): sample for sample in source.samples}
    entries = index.get("documents")
    if not isinstance(entries, list) or not entries:
        raise ParserDatasetError("annotation index documents must be a non-empty list")
    documents: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "document_id", "annotation", "immutable_sha256", "runtime_result", "runtime_result_sha256"
        }:
            raise ParserDatasetError("annotation index entry is invalid")
        relative = Path(str(entry["annotation"] or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ParserDatasetError("annotation path must stay inside annotations directory")
        annotation = _read_json(root / relative, "real annotation")
        document_id = str(entry["document_id"])
        if str(annotation.get("document_id") or "") != document_id:
            raise ParserDatasetError("annotation document_id does not match index")
        runtime_result = Path(str(entry["runtime_result"] or ""))
        if not runtime_result.is_file() or _sha256_file(runtime_result) != str(entry["runtime_result_sha256"] or ""):
            raise ParserDatasetError("runtime OCR result SHA-256 mismatch during finalization")
        source_sample = source_by_id.get(document_id)
        if source_sample is None:
            raise ParserDatasetError("annotation document_id is absent from bound real source")
        runtime_payload = _read_json(runtime_result, "runtime OCR result")
        expected_draft = prepare_real_annotation(source_sample, runtime_payload)
        expected_immutable_sha = annotation_immutable_sha256(expected_draft)
        if expected_immutable_sha != str(entry["immutable_sha256"] or ""):
            raise ParserDatasetError("annotation index immutable SHA-256 disagrees with bound source/runtime snapshot")
        documents.append(finalize_real_annotation(
            annotation,
            expected_immutable_sha256=expected_immutable_sha,
        ))
    manifest = write_parser_dataset(
        output_dir,
        dataset_id=dataset_id,
        documents=documents,
        metadata={
            "builder": "real_annotation_finalize_v1",
            "source_dataset_id": str(index.get("source_dataset_id") or ""),
        },
    )
    dataset = load_parser_dataset(manifest)
    return {
        "status": "ok",
        "manifest": str(manifest),
        "documents": len(dataset.documents),
        "fingerprint": dataset.fingerprint,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_output = bool(args.json)
    try:
        if args.command == "validate":
            dataset = load_parser_dataset(args.manifest, allow_draft=args.allow_draft)
            _emit(
                {
                    "status": "ok",
                    "dataset_id": dataset.dataset_id,
                    "documents": len(dataset.documents),
                    "splits": {
                        name: sum(document["split"] == name for document in dataset.documents)
                        for name in ("train", "val", "test")
                    },
                    "sources": sorted({str(document["source_kind"]) for document in dataset.documents}),
                    "observation_kinds": sorted({str(document["observation"]["kind"]) for document in dataset.documents}),
                    "fingerprint": dataset.fingerprint,
                },
                json_output,
            )
            return 0
        if args.command == "build-synthetic":
            manifest = build_synthetic_dataset(
                truth_samples_path=args.truth_samples,
                output_dir=args.output_dir,
                dataset_id=args.dataset_id,
                observation_kind=args.observation_kind,
                split=args.split,
                seed=args.seed,
            )
            dataset = load_parser_dataset(manifest)
            _emit({"status": "ok", "manifest": str(manifest), "documents": len(dataset.documents), "fingerprint": dataset.fingerprint}, json_output)
            return 0
        if args.command == "build-runtime":
            manifest = build_runtime_dataset(
                truth_samples_path=args.truth_samples,
                results_root=args.results_root,
                output_dir=args.output_dir,
                dataset_id=args.dataset_id,
                split=args.split,
            )
            dataset = load_parser_dataset(manifest)
            _emit({"status": "ok", "manifest": str(manifest), "documents": len(dataset.documents), "fingerprint": dataset.fingerprint}, json_output)
            return 0
        if args.command == "prepare-real":
            _emit(_prepare_real(args.source_manifest, args.results_root, args.output_dir), json_output)
            return 0
        if args.command == "finalize-real":
            _emit(_finalize_real(args.annotations_dir, args.dataset_id, args.output_dir), json_output)
            return 0
        raise ParserDatasetError(f"unsupported command: {args.command}")
    except (OSError, ParserDatasetError, ValueError, TypeError, KeyError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
