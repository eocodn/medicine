from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .real_data import finalize_real_annotation, load_real_source_manifest, prepare_real_annotation
from .training_builders import build_runtime_dataset, build_synthetic_dataset
from .training_dataset import ParserDatasetError, load_parser_dataset, write_parser_dataset


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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
    entries: list[dict[str, str]] = []
    for index, sample in enumerate(source.samples, start=1):
        result_path = root / str(sample["document_id"]) / "result.json"
        result = _read_json(result_path, "runtime OCR result")
        annotation = prepare_real_annotation(sample, result)
        annotation_path = annotations / f"{sample['document_id']}.json"
        _atomic_json(annotation_path, annotation)
        entries.append({"document_id": str(sample["document_id"]), "annotation": annotation_path.name})
        print(f"[ocr-parser-data] real annotation {index}/{len(source.samples)}", file=sys.stderr, flush=True)
    index_value = {
        "schema_version": 1,
        "source_dataset_id": source.dataset_id,
        "source_manifest": str(source.manifest_path),
        "documents": entries,
    }
    _atomic_json(annotations / "index.json", index_value)
    return {"status": "ok", "documents": len(entries), "annotations_dir": str(annotations)}


def _finalize_real(annotations_dir: str, dataset_id: str, output_dir: str) -> dict[str, Any]:
    root = Path(annotations_dir).resolve()
    index = _read_json(root / "index.json", "annotation index")
    entries = index.get("documents")
    if not isinstance(entries, list) or not entries:
        raise ParserDatasetError("annotation index documents must be a non-empty list")
    documents: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"document_id", "annotation"}:
            raise ParserDatasetError("annotation index entry is invalid")
        relative = Path(str(entry["annotation"] or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ParserDatasetError("annotation path must stay inside annotations directory")
        annotation = _read_json(root / relative, "real annotation")
        if str(annotation.get("document_id") or "") != str(entry["document_id"]):
            raise ParserDatasetError("annotation document_id does not match index")
        documents.append(finalize_real_annotation(annotation))
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
