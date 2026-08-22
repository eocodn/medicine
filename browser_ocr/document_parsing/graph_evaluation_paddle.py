from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .evaluation import evaluate_parser_document
from .graph_decode import DecodeConfig
from .graph_inference_paddle import infer_graph_document, load_graph_model
from .training_dataset import ParserDataset, load_parser_dataset


STATE_FILE = "evaluation-state.json"
RESULT_FILE = "result.json"
LOCK_FILE = ".parser-graph-evaluation.lock"
DOCUMENTS_DIR = "documents"
_COUNT_METRICS = (
    "expected_rows",
    "predicted_rows",
    "matched_rows",
    "missing_rows",
    "unexpected_rows",
    "product_exact_rows",
    "product_query_mismatches",
    "field_total",
    "exact_fields",
    "unresolved_fields",
    "false_exact_fields",
    "invented_fields",
    "cross_medication_associations",
    "unproven_associations",
)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"graph evaluation value is not strict JSON: {exc}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read graph evaluation JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"graph evaluation JSON must contain an object: {path}")
    return value


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(_canonical_json(value) + b"\n")
    os.replace(temporary, path)


def _implementation_sha256() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for source in (
        root / "graph_decode.py",
        root / "graph_inference_paddle.py",
        root / "evaluation.py",
        Path(__file__).resolve(),
    ):
        name = source.name.encode("utf-8")
        content = source.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _load_datasets(paths: Sequence[str | Path], *, allow_test: bool) -> list[ParserDataset]:
    if not paths:
        raise ValueError("graph evaluation requires at least one dataset manifest")
    datasets = [load_parser_dataset(path) for path in paths]
    identities: set[tuple[str, str]] = set()
    for dataset in datasets:
        identity = (dataset.dataset_id, dataset.fingerprint)
        if identity in identities:
            raise ValueError("graph evaluation dataset manifests must be unique")
        identities.add(identity)
        splits = {str(document.get("split") or "") for document in dataset.documents}
        if "train" in splits:
            raise ValueError("graph evaluation refuses train documents")
        unsupported = splits - {"val", "test"}
        if unsupported:
            raise ValueError("graph evaluation datasets contain unsupported splits")
        if "test" in splits and not allow_test:
            raise ValueError("graph evaluation test documents require allow_test=True")
    return datasets


def _profile(
    *,
    model_result: Path,
    checkpoint_sha256: str,
    datasets: Sequence[ParserDataset],
    config: DecodeConfig,
    device: str,
    allow_test: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "implementation_sha256": _implementation_sha256(),
        "model_result_sha256": _sha256_file(model_result),
        "model_checkpoint_sha256": checkpoint_sha256,
        "datasets": [
            {"dataset_id": dataset.dataset_id, "fingerprint": dataset.fingerprint}
            for dataset in datasets
        ],
        "decode_config": asdict(config),
        "device": device,
        "allow_test": bool(allow_test),
    }


def _work_items(datasets: Sequence[ParserDataset]) -> list[tuple[ParserDataset, Mapping[str, Any]]]:
    return [
        (dataset, document)
        for dataset in datasets
        for document in dataset.documents
    ]


def _document_path(root: Path, index: int) -> Path:
    return root / DOCUMENTS_DIR / f"document-{index:06d}.json"


def _document_record(
    dataset: ParserDataset,
    document: Mapping[str, Any],
    predicted_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    metrics = evaluate_parser_document(document, predicted_rows)
    observation = document.get("observation")
    observation_kind = str(observation.get("kind") or "") if isinstance(observation, Mapping) else ""
    return {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "dataset_fingerprint": dataset.fingerprint,
        "document_id": str(document.get("document_id") or ""),
        "split": str(document.get("split") or ""),
        "source_kind": str(document.get("source_kind") or ""),
        "observation_kind": observation_kind,
        "layout_family": str(document.get("layout_family") or ""),
        "scenario_tags": list(document.get("scenario_tags") or []),
        "risk_tags": list(document.get("risk_tags") or []),
        "predicted_rows": [dict(row) for row in predicted_rows],
        "metrics": metrics,
    }


def _validate_document_record(
    record: Mapping[str, Any],
    dataset: ParserDataset,
    document: Mapping[str, Any],
) -> None:
    if record.get("schema_version") != 1:
        raise ValueError("graph evaluation document result schema mismatch")
    expected = {
        "dataset_id": dataset.dataset_id,
        "dataset_fingerprint": dataset.fingerprint,
        "document_id": str(document.get("document_id") or ""),
        "split": str(document.get("split") or ""),
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise ValueError(f"graph evaluation document result {field} mismatch")
    predicted = record.get("predicted_rows")
    if not isinstance(predicted, list):
        raise ValueError("graph evaluation document result predictions are invalid")
    if record.get("metrics") != evaluate_parser_document(document, predicted):
        raise ValueError("graph evaluation document metrics do not match predictions")


def _checkpoint_summary(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(record["document_id"]),
        "dataset_id": str(record["dataset_id"]),
        "path": str(path),
        "sha256": _sha256_file(path),
    }


def _adopt_results(
    root: Path,
    state: dict[str, Any],
    items: Sequence[tuple[ParserDataset, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    history = list(state.get("history") or [])
    completed = int(state.get("completed_documents") or 0)
    if len(history) != completed or completed > len(items):
        raise ValueError("graph evaluation state history disagrees with completed_documents")
    for index in range(1, completed + 1):
        path = _document_path(root, index)
        if not path.is_file() or history[index - 1].get("sha256") != _sha256_file(path):
            raise ValueError(f"graph evaluation completed document {index} hash mismatch")
        record = _json_object(path)
        dataset, document = items[index - 1]
        _validate_document_record(record, dataset, document)
    for index in range(completed + 1, len(items) + 1):
        path = _document_path(root, index)
        if not path.is_file():
            break
        record = _json_object(path)
        dataset, document = items[index - 1]
        _validate_document_record(record, dataset, document)
        history.append(_checkpoint_summary(path, record))
    state["completed_documents"] = len(history)
    state["history"] = history
    return history


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {key: 0 for key in _COUNT_METRICS}
    for record in records:
        metrics = record.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError("graph evaluation document metrics are missing")
        for key in _COUNT_METRICS:
            value = metrics.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"graph evaluation metric {key} is invalid")
            totals[key] += value
    expected_rows = totals["expected_rows"]
    field_total = totals["field_total"]
    safety_pass = (
        totals["false_exact_fields"] == 0
        and totals["unexpected_rows"] == 0
        and totals["cross_medication_associations"] == 0
        and totals["unproven_associations"] == 0
        and totals["product_query_mismatches"] == 0
    )
    return {
        **totals,
        "row_recall": totals["matched_rows"] / expected_rows if expected_rows else 1.0,
        "field_exact_accuracy": totals["exact_fields"] / field_total if field_total else 1.0,
        "safety_pass": safety_pass,
    }


def _completed_result(
    root: Path,
    profile: Mapping[str, Any],
    state: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    result_path = root / RESULT_FILE
    if not result_path.is_file() or state.get("result_sha256") != _sha256_file(result_path):
        raise ValueError("completed graph evaluation result SHA-256 mismatch")
    result = _json_object(result_path)
    if result.get("status") != "ok" or result.get("profile") != profile:
        raise ValueError("completed graph evaluation result profile mismatch")
    if result.get("documents") != list(history):
        raise ValueError("completed graph evaluation result/history mismatch")
    records = [_json_object(Path(str(item["path"]))) for item in history]
    if result.get("metrics") != _aggregate(records):
        raise ValueError("completed graph evaluation aggregate metrics mismatch")
    return result


def run_graph_evaluation(
    *,
    model_result: str | Path,
    dataset_manifests: Sequence[str | Path],
    output_dir: str | Path,
    config: DecodeConfig = DecodeConfig(),
    device: str = "gpu",
    allow_test: bool = False,
) -> dict[str, Any]:
    if device not in {"cpu", "gpu"}:
        raise ValueError("graph evaluation device must be cpu or gpu")
    model_path = Path(model_result).resolve()
    datasets = _load_datasets(dataset_manifests, allow_test=allow_test)
    bundle = load_graph_model(model_path, device=device)
    profile = _profile(
        model_result=model_path,
        checkpoint_sha256=bundle.checkpoint_sha256,
        datasets=datasets,
        config=config,
        device=device,
        allow_test=allow_test,
    )
    profile_sha256 = _sha256_bytes(_canonical_json(profile))
    items = _work_items(datasets)
    if not items:
        raise ValueError("graph evaluation datasets contain no documents")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_FILE
    state_path = root / STATE_FILE
    result_path = root / RESULT_FILE
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"graph evaluation is already active in {root}") from exc

        if state_path.is_file():
            state = _json_object(state_path)
            if state.get("profile") != profile or state.get("profile_sha256") != profile_sha256:
                raise ValueError("graph evaluation profile differs from existing state")
            if state.get("status") == "completed":
                history = _adopt_results(root, state, items)
                return _completed_result(root, profile, state, history)
            if state.get("status") not in {"running", "failed"}:
                raise ValueError("graph evaluation state has unsupported status")
        else:
            unexpected = [path.name for path in root.iterdir() if path.name != LOCK_FILE]
            if unexpected:
                raise ValueError("graph evaluation output is non-empty without authoritative state")
            state = {
                "schema_version": 1,
                "status": "running",
                "profile": profile,
                "profile_sha256": profile_sha256,
                "completed_documents": 0,
                "history": [],
            }
            _atomic_json(state_path, state)

        history = _adopt_results(root, state, items)
        state.update(status="running", completed_documents=len(history), history=history)
        state.pop("last_error", None)
        _atomic_json(state_path, state)
        try:
            for index in range(len(history), len(items)):
                dataset, document = items[index]
                predicted_rows = infer_graph_document(bundle, document, config=config)
                record = _document_record(dataset, document, predicted_rows)
                path = _document_path(root, index + 1)
                _atomic_json(path, record)
                history.append(_checkpoint_summary(path, record))
                state.update(completed_documents=len(history), history=history)
                _atomic_json(state_path, state)
                print(
                    f"[ocr-parser-eval] {len(history)}/{len(items)} "
                    f"{dataset.dataset_id}/{document['document_id']}",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as exc:
            state.update(
                status="failed",
                completed_documents=len(history),
                history=history,
                last_error=str(exc)[:1000],
            )
            _atomic_json(state_path, state)
            raise

        records = [_json_object(_document_path(root, index)) for index in range(1, len(items) + 1)]
        metrics = _aggregate(records)
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "document_count": len(records),
            "metrics": metrics,
            "documents": history,
        }
        _atomic_json(result_path, result)
        state.update(
            status="completed",
            completed_documents=len(history),
            history=history,
            result_sha256=_sha256_file(result_path),
        )
        state.pop("last_error", None)
        _atomic_json(state_path, state)
        return result


__all__ = ["run_graph_evaluation"]