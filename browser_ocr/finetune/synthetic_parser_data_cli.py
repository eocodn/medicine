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

from browser_ocr.document_parsing.observation_profile import runtime_observation_producer
from browser_ocr.document_parsing.training_builders import build_runtime_dataset
from browser_ocr.document_parsing.training_dataset import ParserDatasetError, load_parser_dataset

from .full_document_cli import build_ocr_producer_profile
from .full_document_runtime import FullDocumentRuntime


LOCK_FILE = ".synthetic-parser-runtime.lock"


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


def _json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ParserDatasetError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParserDatasetError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ParserDatasetError(f"{label} must be an object: {path}")
    return value


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ParserDatasetError(f"synthetic parser OCR batch is already active in {path.parent}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-synthetic-runtime",
        description="Run the selected full-document OCR producer over a unified synthetic corpus and build GT-aligned runtime parser datasets",
    )
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--truth-samples", required=True)
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


def _load_truth(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not path.is_file():
        raise ParserDatasetError(f"parser truth samples do not exist: {path}")
    ordered: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ParserDatasetError(f"parser truth line {line_number} is invalid JSON") from exc
        if not isinstance(sample, dict):
            raise ParserDatasetError(f"parser truth line {line_number} must be an object")
        document_id = str(sample.get("document_id") or "")
        if not document_id or document_id in by_id:
            raise ParserDatasetError("parser truth document ids must be non-empty and unique")
        if sample.get("split") not in {"train", "val", "test"}:
            raise ParserDatasetError(f"parser truth split is invalid for {document_id}")
        image_sha = str(sample.get("image_sha256") or "")
        if len(image_sha) != 64 or any(char not in "0123456789abcdef" for char in image_sha):
            raise ParserDatasetError(f"parser truth image SHA-256 is invalid for {document_id}")
        ordered.append(sample)
        by_id[document_id] = sample
    if not ordered:
        raise ParserDatasetError("parser truth samples are empty")
    return ordered, by_id


def _load_source(corpus_manifest: Path, truth_samples: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corpus = _json_object(corpus_manifest, "unified corpus manifest")
    if corpus.get("schema_version") != 3 or corpus.get("synthetic_only") is not True:
        raise ParserDatasetError("synthetic runtime parser batch requires a schema-v3 synthetic-only corpus")
    corpus_id = str(corpus.get("corpus_id") or "")
    raw_samples = corpus.get("samples")
    if not corpus_id or not isinstance(raw_samples, list) or not raw_samples:
        raise ParserDatasetError("unified corpus manifest is missing corpus identity or samples")
    _, truth_by_id = _load_truth(truth_samples)
    corpus_root = corpus_manifest.parent.resolve()
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_samples):
        if not isinstance(raw, Mapping):
            raise ParserDatasetError(f"unified corpus sample {index} must be an object")
        document_id = str(raw.get("id") or "")
        if not document_id or document_id in seen:
            raise ParserDatasetError("unified corpus document ids must be non-empty and unique")
        seen.add(document_id)
        truth = truth_by_id.get(document_id)
        if truth is None:
            raise ParserDatasetError("unified corpus and parser truth document set differ")
        split = str(raw.get("split") or "")
        image_sha = str(raw.get("image_sha256") or "")
        if split != truth.get("split") or image_sha != truth.get("image_sha256"):
            raise ParserDatasetError(f"unified corpus and parser truth binding differs for {document_id}")
        relative = Path(str(raw.get("image") or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ParserDatasetError(f"unified corpus image path is unsafe for {document_id}")
        image_path = (corpus_root / relative).resolve()
        try:
            image_path.relative_to(corpus_root)
        except ValueError as exc:
            raise ParserDatasetError(f"unified corpus image path escapes corpus root for {document_id}") from exc
        ordered.append({
            "document_id": document_id,
            "split": split,
            "image_sha256": image_sha,
            "width": int(raw.get("width") or 0),
            "height": int(raw.get("height") or 0),
            "image_path": image_path,
        })
    if seen != set(truth_by_id):
        raise ParserDatasetError("unified corpus and parser truth document set differ")
    return corpus, ordered


def _batch_profile(args: argparse.Namespace, corpus: Mapping[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "corpus_id": str(corpus["corpus_id"]),
        "corpus_manifest_sha256": _sha256_file(Path(args.corpus_manifest).resolve()),
        "truth_samples_sha256": _sha256_file(Path(args.truth_samples).resolve()),
        "document_count": len(samples),
        "ocr_producer": build_ocr_producer_profile(args),
    }


def _validate_runtime_result(
    result_path: Path,
    *,
    sample: Mapping[str, Any],
    producer: Mapping[str, Any],
) -> dict[str, Any]:
    result = _json_object(result_path, "runtime OCR result")
    if result.get("status") != "ok":
        raise ParserDatasetError(f"runtime OCR result is not successful for {sample['document_id']}")
    current_producer = runtime_observation_producer(
        result.get("profile"),
        expected_image_sha256=str(sample["image_sha256"]),
    )
    if current_producer != producer:
        raise ParserDatasetError("runtime OCR producer differs from synthetic batch profile")
    image = result.get("image")
    if not isinstance(image, Mapping) or str(image.get("sha256") or "") != str(sample["image_sha256"]):
        raise ParserDatasetError(f"runtime OCR image binding differs for {sample['document_id']}")
    if int(image.get("source_width") or 0) != int(sample["width"]) or int(image.get("source_height") or 0) != int(sample["height"]):
        raise ParserDatasetError(f"runtime OCR source image dimensions differ for {sample['document_id']}")
    stages = result.get("stages")
    orientation = stages.get("orientation") if isinstance(stages, Mapping) else None
    rotation = orientation.get("applied_rotation_degrees") if isinstance(orientation, Mapping) else None
    if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in {0, 90, 180, 270}:
        raise ParserDatasetError(f"runtime OCR orientation is invalid for {sample['document_id']}")
    expected_width = int(sample["height"]) if rotation in {90, 270} else int(sample["width"])
    expected_height = int(sample["width"]) if rotation in {90, 270} else int(sample["height"])
    if int(image.get("width") or 0) != expected_width or int(image.get("height") or 0) != expected_height:
        raise ParserDatasetError(f"runtime OCR canonical image dimensions differ for {sample['document_id']}")
    if not isinstance(result.get("regions"), list):
        raise ParserDatasetError(f"runtime OCR result regions are missing for {sample['document_id']}")
    return result


def _validate_dataset(path: Path, *, split: str, producer: Mapping[str, Any], expected_count: int) -> None:
    dataset = load_parser_dataset(path)
    if len(dataset.documents) != expected_count:
        raise ParserDatasetError(f"runtime parser {split} dataset document count differs")
    for document in dataset.documents:
        if document["split"] != split or document["observation"]["kind"] != "runtime_ocr":
            raise ParserDatasetError(f"runtime parser {split} dataset has an invalid observation/split")
        current = runtime_observation_producer(
            document["observation"]["profile"],
            expected_image_sha256=document["image_sha256"],
        )
        if current != producer:
            raise ParserDatasetError(f"runtime parser {split} dataset producer differs")


def _validate_completed(
    *,
    output: Path,
    state: Mapping[str, Any],
    profile: Mapping[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    result_path = output / "result.json"
    expected_result_sha = str(state.get("result_sha256") or "")
    if len(expected_result_sha) != 64 or not result_path.is_file() or _sha256_file(result_path) != expected_result_sha:
        raise ParserDatasetError("completed synthetic parser batch result SHA-256 mismatch")
    result = _json_object(result_path, "synthetic parser batch result")
    if result.get("status") != "ok" or result.get("profile") != profile or result.get("documents") != len(samples):
        raise ParserDatasetError("completed synthetic parser batch state/result disagree")
    runtime_hashes = result.get("runtime_results")
    if not isinstance(runtime_hashes, Mapping) or set(runtime_hashes) != {sample["document_id"] for sample in samples}:
        raise ParserDatasetError("completed synthetic parser batch runtime result set differs")
    producer = profile["ocr_producer"]
    for sample in samples:
        result_path = output / "runtime" / sample["document_id"] / "result.json"
        if not result_path.is_file() or _sha256_file(result_path) != runtime_hashes[sample["document_id"]]:
            raise ParserDatasetError("completed synthetic runtime OCR result SHA-256 mismatch")
        _validate_runtime_result(result_path, sample=sample, producer=producer)
    datasets = result.get("datasets")
    if not isinstance(datasets, Mapping):
        raise ParserDatasetError("completed synthetic parser batch datasets are missing")
    split_counts = {name: sum(sample["split"] == name for sample in samples) for name in ("train", "val", "test")}
    expected_splits = {name for name, count in split_counts.items() if count}
    if set(datasets) != expected_splits:
        raise ParserDatasetError("completed synthetic parser batch dataset split set differs")
    for split in expected_splits:
        _validate_dataset(Path(str(datasets[split])), split=split, producer=producer, expected_count=split_counts[split])
    return result


def run_synthetic_batch(args: argparse.Namespace) -> dict[str, Any]:
    corpus_manifest = Path(args.corpus_manifest).resolve()
    truth_samples = Path(args.truth_samples).resolve()
    corpus, samples = _load_source(corpus_manifest, truth_samples)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    profile = _batch_profile(args, corpus, samples)
    state_path = output / "state.json"
    runtime_root = output / "runtime"

    with _exclusive_lock(output / LOCK_FILE):
        completed = 0
        completed_results: list[dict[str, str]] = []
        if state_path.is_file():
            state = _json_object(state_path, "synthetic parser batch state")
            if state.get("profile") != profile:
                raise ParserDatasetError("synthetic parser batch output profile differs from requested inputs/models")
            completed = state.get("completed")
            completed_results = state.get("runtime_results")
            if isinstance(completed, bool) or not isinstance(completed, int) or not 0 <= completed <= len(samples):
                raise ParserDatasetError("synthetic parser batch completed counter is invalid")
            if not isinstance(completed_results, list) or len(completed_results) != completed:
                raise ParserDatasetError("synthetic parser batch runtime checkpoint is invalid")
            if state.get("status") == "completed":
                return _validate_completed(output=output, state=state, profile=profile, samples=samples)
            if state.get("status") != "running":
                raise ParserDatasetError("synthetic parser batch state has unsupported status")
        else:
            unexpected = [path.name for path in output.iterdir() if path.name != LOCK_FILE]
            if unexpected:
                raise ParserDatasetError("synthetic parser batch output is non-empty without authoritative state")
            _atomic_json(state_path, {
                "schema_version": 1,
                "status": "running",
                "profile": profile,
                "completed": 0,
                "runtime_results": [],
            })

        producer = profile["ocr_producer"]
        runtime: FullDocumentRuntime | None = None
        for index, sample in enumerate(samples, start=1):
            image_path = Path(sample["image_path"])
            if not image_path.is_file() or _sha256_file(image_path) != sample["image_sha256"]:
                raise ParserDatasetError(f"synthetic source image SHA-256 mismatch for {sample['document_id']}")
            result_path = runtime_root / sample["document_id"] / "result.json"
            if index <= completed:
                checkpoint = completed_results[index - 1]
                if not isinstance(checkpoint, Mapping) or checkpoint.get("document_id") != sample["document_id"]:
                    raise ParserDatasetError("synthetic parser batch checkpoint document order differs")
                if not result_path.is_file() or _sha256_file(result_path) != checkpoint.get("sha256"):
                    raise ParserDatasetError("synthetic parser batch checkpoint runtime result SHA-256 mismatch")
                _validate_runtime_result(result_path, sample=sample, producer=producer)
                continue

            if result_path.is_file():
                _validate_runtime_result(result_path, sample=sample, producer=producer)
            else:
                if runtime is None:
                    runtime = FullDocumentRuntime(args)
                returned = runtime.run(
                    image_path=image_path,
                    output_dir=runtime_root / sample["document_id"],
                )
                persisted = _validate_runtime_result(result_path, sample=sample, producer=producer)
                if returned != persisted:
                    raise ParserDatasetError("runtime OCR returned result differs from persisted result")

            completed_results.append({"document_id": sample["document_id"], "sha256": _sha256_file(result_path)})
            completed = index
            _atomic_json(state_path, {
                "schema_version": 1,
                "status": "running",
                "profile": profile,
                "completed": completed,
                "runtime_results": completed_results,
            })
            print(f"[ocr-parser-synthetic-runtime] {index}/{len(samples)} {sample['document_id']}", file=sys.stderr, flush=True)

        split_counts = {name: sum(sample["split"] == name for sample in samples) for name in ("train", "val", "test")}
        datasets: dict[str, str] = {}
        for split, count in split_counts.items():
            if not count:
                continue
            manifest = build_runtime_dataset(
                truth_samples_path=truth_samples,
                results_root=runtime_root,
                output_dir=output / "datasets" / split,
                dataset_id=f"{corpus['corpus_id']}-runtime-{split}",
                split=split,
            )
            _validate_dataset(manifest, split=split, producer=producer, expected_count=count)
            datasets[split] = str(manifest)

        runtime_hashes = {
            item["document_id"]: item["sha256"]
            for item in completed_results
        }
        result = {
            "status": "ok",
            "documents": len(samples),
            "splits": split_counts,
            "runtime_root": str(runtime_root),
            "runtime_results": runtime_hashes,
            "datasets": datasets,
            "profile": profile,
        }
        result_path = output / "result.json"
        _atomic_json(result_path, result)
        _atomic_json(state_path, {
            "schema_version": 1,
            "status": "completed",
            "profile": profile,
            "completed": len(samples),
            "runtime_results": completed_results,
            "result_sha256": _sha256_file(result_path),
        })
        return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_synthetic_batch(args)
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


__all__ = ["build_parser", "run_synthetic_batch"]