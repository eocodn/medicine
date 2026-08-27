from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import paddle

from .artifact_storage import atomic_write
from .parser_v5_dataset import load_parser_v5_dataset
from .parser_v5_decode import ParserV5DecodeConfig
from .parser_v5_evaluation import evaluate_parser_v5_rows
from .parser_v5_inference_paddle import run_parser_v5_inference
from .parser_v5_model_input import build_parser_v5_runtime_input
from .parser_v5_training_paddle import ParserV5Model, ParserV5TrainingConfig
from .parser_v5_validation_protocol import (
    validate_parser_v5_frozen_implementation,
    validate_parser_v5_holdout_authorization,
)


_OUTPUT_FIELDS = {
    "schema_version",
    "status",
    "holdout_id",
    "candidate_freeze_fingerprint",
    "holdout_open_fingerprint",
    "holdout_samples_sha256",
    "partition_fingerprint",
    "training_result_sha256",
    "checkpoint_sha256",
    "decode_policy",
    "documents",
    "metrics",
    "evaluation_fingerprint",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {source}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def _runtime_nodes(observation: Mapping[str, Any]) -> list[dict[str, object]]:
    raw_nodes = observation.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("Parser v5 sealed observation nodes must be a list")
    return [
        {
            "node_id": str(node["node_id"]),
            "text": str(node["text"]),
            "detector_confidence": float(node["detector_confidence"]),
            "recognizer_confidence": float(node["recognizer_confidence"]),
            "polygon": node["polygon"],
        }
        for node in raw_nodes
    ]


def _aggregate_metrics(items: Sequence[Mapping[str, int | float]]) -> dict[str, int | float]:
    integer_fields = (
        "gold_rows",
        "predicted_rows",
        "product_tp",
        "product_fp",
        "product_fn",
        "field_total",
        "field_exact",
        "field_false_exact",
        "field_unresolved",
        "cross_medication_associations",
        "invented_values",
        "zero_medication_false_rows",
    )
    totals = {field: sum(int(item[field]) for item in items) for field in integer_fields}
    product_precision = totals["product_tp"] / max(totals["product_tp"] + totals["product_fp"], 1)
    product_recall = totals["product_tp"] / max(totals["product_tp"] + totals["product_fn"], 1)
    field_exact_rate = totals["field_exact"] / max(totals["field_total"], 1)
    field_unresolved_rate = totals["field_unresolved"] / max(totals["field_total"], 1)
    return {
        **totals,
        "product_precision": product_precision,
        "product_recall": product_recall,
        "field_exact_rate": field_exact_rate,
        "field_unresolved_rate": field_unresolved_rate,
    }


def _load_frozen_model(
    *,
    training_result: str | Path,
    freeze: Mapping[str, Any],
) -> tuple[ParserV5Model, ParserV5TrainingConfig, str]:
    result_path = Path(training_result).resolve()
    result = _json_object(result_path, label="Parser v5 training result")
    result_sha256 = _sha256_file(result_path)
    if result_sha256 != freeze["training_result_sha256"]:
        raise ValueError("Parser v5 sealed evaluation training result disagrees with candidate freeze")
    if result.get("status") != "ok" or result.get("schema_version") != 1:
        raise ValueError("Parser v5 sealed evaluation requires a completed training result")
    profile = result.get("profile")
    if not isinstance(profile, Mapping) or result.get("profile_sha256") != freeze["training_profile_sha256"]:
        raise ValueError("Parser v5 sealed evaluation training profile disagrees with candidate freeze")
    config_raw = profile.get("config")
    if not isinstance(config_raw, Mapping):
        raise ValueError("Parser v5 sealed evaluation training config is invalid")
    config = ParserV5TrainingConfig(**dict(config_raw))
    checkpoint = Path(str(result.get("best_checkpoint") or "")).resolve()
    if not checkpoint.is_file() or _sha256_file(checkpoint) != freeze["checkpoint_sha256"]:
        raise ValueError("Parser v5 sealed evaluation checkpoint disagrees with candidate freeze")
    paddle.set_device(config.device)
    with paddle.utils.unique_name.guard():
        model = ParserV5Model(config)
    model.set_state_dict(paddle.load(str(checkpoint)))
    model.eval()
    return model, config, result_sha256


def _validate_existing_output(path: Path, expected_fingerprint: str) -> dict[str, Any]:
    value = _json_object(path, label="Parser v5 sealed evaluation result")
    if set(value) != _OUTPUT_FIELDS or value.get("schema_version") != 1 or value.get("status") != "ok":
        raise ValueError("Parser v5 sealed evaluation result fields are invalid")
    payload = {key: value[key] for key in value if key != "evaluation_fingerprint"}
    actual = _sha256_bytes(_canonical_json(payload))
    if actual != value.get("evaluation_fingerprint") or actual != expected_fingerprint:
        raise ValueError("Parser v5 sealed evaluation result fingerprint mismatch")
    return value


def evaluate_parser_v5_sealed_holdout(
    *,
    candidate_freeze: str | Path,
    training_result: str | Path,
    holdout_envelope: str | Path,
    open_record: str | Path,
    holdout_manifest: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    freeze, envelope, opened = validate_parser_v5_holdout_authorization(
        candidate_freeze=candidate_freeze,
        holdout_envelope=holdout_envelope,
        open_record=open_record,
    )
    validate_parser_v5_frozen_implementation(freeze)
    holdout = load_parser_v5_dataset(holdout_manifest)
    if holdout.samples_sha256 != envelope["samples_sha256"]:
        raise ValueError("Parser v5 sealed holdout artifact SHA-256 disagrees with opened envelope")
    if len(holdout.samples) != envelope["document_count"]:
        raise ValueError("Parser v5 sealed holdout artifact count disagrees with opened envelope")

    model, config, training_result_sha256 = _load_frozen_model(training_result=training_result, freeze=freeze)
    decode_config = ParserV5DecodeConfig(**dict(freeze["decode_policy"]))
    metrics: list[Mapping[str, int | float]] = []
    for sample in holdout.samples:
        truth = sample["truth"]
        observation = sample["observation"]
        nodes = _runtime_nodes(observation)
        if nodes:
            model_input = build_parser_v5_runtime_input(
                document_id=str(truth["document_id"]),
                width=truth["width"],
                height=truth["height"],
                nodes=nodes,
                max_text_bytes=config.max_text_bytes,
            )
            inference = run_parser_v5_inference(
                encoder=model.encoder,
                heads=model.heads,
                model_input=model_input,
                nodes=nodes,
                config=decode_config,
            )
            rows = inference.rows
        else:
            rows = ()
        metrics.append(evaluate_parser_v5_rows(truth, observation, rows))

    payload = {
        "schema_version": 1,
        "status": "ok",
        "holdout_id": str(envelope["holdout_id"]),
        "candidate_freeze_fingerprint": str(freeze["freeze_fingerprint"]),
        "holdout_open_fingerprint": str(opened["open_fingerprint"]),
        "holdout_samples_sha256": holdout.samples_sha256,
        "partition_fingerprint": str(envelope["partition_fingerprint"]),
        "training_result_sha256": training_result_sha256,
        "checkpoint_sha256": str(freeze["checkpoint_sha256"]),
        "decode_policy": asdict(decode_config),
        "documents": len(holdout.samples),
        "metrics": _aggregate_metrics(metrics),
    }
    fingerprint = _sha256_bytes(_canonical_json(payload))
    result = {**payload, "evaluation_fingerprint": fingerprint}
    destination = Path(output_path).resolve()
    if destination.exists():
        return _validate_existing_output(destination, fingerprint)
    atomic_write(destination, _canonical_json(result) + b"\n")
    return result


__all__ = ["evaluate_parser_v5_sealed_holdout"]