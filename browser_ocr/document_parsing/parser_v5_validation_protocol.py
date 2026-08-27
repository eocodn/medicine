from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_storage import atomic_write
from .parser_v5_calibration import load_parser_v5_calibration
from .parser_v5_dataset import load_parser_v5_dataset
from .parser_v5_decode import ParserV5DecodeConfig
from .parser_v5_development_views import DEVELOPMENT_VIEWS


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FREEZE_FIELDS = {
    "schema_version",
    "status",
    "model_id",
    "training_result_sha256",
    "training_profile_sha256",
    "checkpoint_sha256",
    "train_datasets",
    "development_views",
    "calibration_fingerprint",
    "runtime_producer_fingerprint",
    "decode_policy",
    "implementation_sha256",
    "freeze_fingerprint",
}
_HOLDOUT_FIELDS = {
    "schema_version",
    "holdout_id",
    "samples_sha256",
    "document_count",
    "partition_fingerprint",
}
_OPEN_FIELDS = {
    "schema_version",
    "status",
    "holdout_id",
    "holdout_envelope_sha256",
    "holdout_samples_sha256",
    "partition_fingerprint",
    "candidate_freeze_fingerprint",
    "open_fingerprint",
}
_IMPLEMENTATION_FILES = (
    "parser_v5_world.py",
    "parser_v5_observation.py",
    "parser_v5_contract.py",
    "parser_v5_dataset.py",
    "parser_v5_model_input.py",
    "parser_v5_encoder_paddle.py",
    "parser_v5_structured_targets.py",
    "parser_v5_heads_paddle.py",
    "parser_v5_training_paddle.py",
    "parser_v5_decode.py",
    "parser_v5_inference_paddle.py",
    "parser_v5_evaluation.py",
    "parser_v5_development_views.py",
    "parser_v5_calibration.py",
)


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


def _json_file(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {source}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def _require_sha(value: object, label: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return text


def _dataset_identities(value: object, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    identities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"dataset_id", "samples_sha256"}:
            raise ValueError(f"{label} entries are invalid")
        dataset_id = str(raw.get("dataset_id") or "").strip()
        if not dataset_id:
            raise ValueError(f"{label} dataset_id is required")
        samples_sha256 = _require_sha(raw.get("samples_sha256"), f"{label} samples_sha256")
        identity = (dataset_id, samples_sha256)
        if identity in seen:
            raise ValueError(f"{label} entries must be unique")
        seen.add(identity)
        identities.append({"dataset_id": dataset_id, "samples_sha256": samples_sha256})
    identities.sort(key=lambda item: item["dataset_id"])
    return identities


def _implementation_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    values: dict[str, str] = {}
    for name in _IMPLEMENTATION_FILES:
        source = root / name
        if not source.is_file():
            raise ValueError(f"Parser v5 freeze implementation source is missing: {name}")
        values[name] = _sha256_file(source)
    return values


def freeze_parser_v5_candidate(
    *,
    training_result: str | Path,
    development_manifests: Sequence[str | Path],
    calibration_artifact: str | Path,
    output_path: str | Path,
) -> Path:
    result_path = Path(training_result).resolve()
    result = _json_file(result_path, label="Parser v5 training result")
    if result.get("schema_version") != 1 or result.get("status") != "ok":
        raise ValueError("Parser v5 candidate freeze requires a completed training result")
    profile = result.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("Parser v5 training result profile is invalid")
    profile_sha256 = _sha256_bytes(_canonical_json(profile))
    if result.get("profile_sha256") != profile_sha256:
        raise ValueError("Parser v5 training profile SHA-256 mismatch")
    if profile.get("model_id") != "parser_v5_global_structured_v1":
        raise ValueError("Parser v5 training result model_id is unsupported")
    train_datasets = _dataset_identities(profile.get("train_datasets"), "Parser v5 freeze train datasets")
    validation_datasets = _dataset_identities(
        profile.get("validation_datasets"), "Parser v5 freeze validation datasets"
    )
    checkpoint = Path(str(result.get("best_checkpoint") or "")).resolve()
    if not checkpoint.is_file():
        raise ValueError("Parser v5 freeze best checkpoint does not exist")
    checkpoint_sha256 = _sha256_file(checkpoint)
    if result.get("best_checkpoint_sha256") != checkpoint_sha256:
        raise ValueError("Parser v5 freeze best checkpoint SHA-256 mismatch")

    development = [load_parser_v5_dataset(path) for path in development_manifests]
    development_views = sorted(
        (
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256}
            for dataset in development
        ),
        key=lambda item: item["dataset_id"],
    )
    expected_ids = {f"dev-{view.name.replace('_', '-')}" for view in DEVELOPMENT_VIEWS}
    actual_ids = {item["dataset_id"] for item in development_views}
    if actual_ids != expected_ids:
        raise ValueError("Parser v5 freeze requires the complete development-view matrix")
    if development_views != validation_datasets:
        raise ValueError("Parser v5 freeze development views disagree with training validation profile")
    best_validation = result.get("best_validation")
    views = best_validation.get("views") if isinstance(best_validation, Mapping) else None
    if not isinstance(views, Mapping) or set(views) != expected_ids:
        raise ValueError("Parser v5 freeze best validation does not cover the complete development matrix")

    calibration = load_parser_v5_calibration(calibration_artifact)
    train_hashes = {item["samples_sha256"] for item in train_datasets}
    if calibration["dataset_samples_sha256"] not in train_hashes:
        raise ValueError("Parser v5 freeze calibration is not bound to a training dataset")

    payload = {
        "schema_version": 1,
        "status": "frozen",
        "model_id": str(profile["model_id"]),
        "training_result_sha256": _sha256_file(result_path),
        "training_profile_sha256": profile_sha256,
        "checkpoint_sha256": checkpoint_sha256,
        "train_datasets": train_datasets,
        "development_views": development_views,
        "calibration_fingerprint": str(calibration["calibration_fingerprint"]),
        "runtime_producer_fingerprint": str(calibration["producer_fingerprint"]),
        "decode_policy": asdict(ParserV5DecodeConfig()),
        "implementation_sha256": _implementation_hashes(),
    }
    freeze = {**payload, "freeze_fingerprint": _sha256_bytes(_canonical_json(payload))}
    destination = Path(output_path).resolve()
    atomic_write(destination, _canonical_json(freeze) + b"\n")
    return destination


def load_parser_v5_candidate_freeze(path: str | Path) -> dict[str, Any]:
    value = _json_file(path, label="Parser v5 candidate freeze")
    if set(value) != _FREEZE_FIELDS:
        raise ValueError("Parser v5 candidate freeze fields are invalid")
    if value.get("schema_version") != 1 or value.get("status") != "frozen":
        raise ValueError("Parser v5 candidate freeze state is invalid")
    for field in (
        "training_result_sha256",
        "training_profile_sha256",
        "checkpoint_sha256",
        "calibration_fingerprint",
        "runtime_producer_fingerprint",
        "freeze_fingerprint",
    ):
        _require_sha(value.get(field), f"Parser v5 freeze {field}")
    _dataset_identities(value.get("train_datasets"), "Parser v5 freeze train datasets")
    development = _dataset_identities(value.get("development_views"), "Parser v5 freeze development views")
    expected_ids = {f"dev-{view.name.replace('_', '-')}" for view in DEVELOPMENT_VIEWS}
    if {item["dataset_id"] for item in development} != expected_ids:
        raise ValueError("Parser v5 candidate freeze development matrix is incomplete")
    decode_policy = value.get("decode_policy")
    if not isinstance(decode_policy, Mapping):
        raise ValueError("Parser v5 candidate freeze decode policy is invalid")
    ParserV5DecodeConfig(**dict(decode_policy))
    implementation = value.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or set(implementation) != set(_IMPLEMENTATION_FILES):
        raise ValueError("Parser v5 candidate freeze implementation identity is invalid")
    for name, sha in implementation.items():
        _require_sha(sha, f"Parser v5 freeze implementation {name}")
    payload = {key: value[key] for key in value if key != "freeze_fingerprint"}
    if _sha256_bytes(_canonical_json(payload)) != value["freeze_fingerprint"]:
        raise ValueError("Parser v5 candidate freeze fingerprint mismatch")
    return value


def _holdout_envelope(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path).resolve()
    value = _json_file(source, label="Parser v5 sealed holdout envelope")
    if set(value) != _HOLDOUT_FIELDS or value.get("schema_version") != 1:
        raise ValueError("Parser v5 sealed holdout envelope fields are invalid")
    holdout_id = str(value.get("holdout_id") or "").strip()
    if not holdout_id.startswith("sealed-"):
        raise ValueError("Parser v5 sealed holdout_id must start with sealed-")
    _require_sha(value.get("samples_sha256"), "Parser v5 sealed holdout samples_sha256")
    _require_sha(value.get("partition_fingerprint"), "Parser v5 sealed holdout partition_fingerprint")
    count = value.get("document_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("Parser v5 sealed holdout document_count must be positive")
    return value, _sha256_file(source)


def _load_open_record(path: Path) -> dict[str, Any]:
    value = _json_file(path, label="Parser v5 holdout open record")
    if set(value) != _OPEN_FIELDS or value.get("schema_version") != 1 or value.get("status") != "opened":
        raise ValueError("Parser v5 holdout open record fields are invalid")
    for field in (
        "holdout_envelope_sha256",
        "holdout_samples_sha256",
        "partition_fingerprint",
        "candidate_freeze_fingerprint",
        "open_fingerprint",
    ):
        _require_sha(value.get(field), f"Parser v5 holdout open record {field}")
    payload = {key: value[key] for key in value if key != "open_fingerprint"}
    if _sha256_bytes(_canonical_json(payload)) != value["open_fingerprint"]:
        raise ValueError("Parser v5 holdout open record fingerprint mismatch")
    return value


def authorize_parser_v5_holdout_open(
    *,
    candidate_freeze: str | Path,
    holdout_envelope: str | Path,
    open_record: str | Path,
    unlock_holdout_id: str,
) -> dict[str, Any]:
    freeze = load_parser_v5_candidate_freeze(candidate_freeze)
    envelope, envelope_sha256 = _holdout_envelope(holdout_envelope)
    holdout_id = str(envelope["holdout_id"])
    if unlock_holdout_id != holdout_id:
        raise ValueError("Parser v5 sealed holdout unlock id does not match the envelope")
    destination = Path(open_record).resolve()
    if destination.exists():
        existing = _load_open_record(destination)
        same_holdout = (
            existing["holdout_id"] == holdout_id
            and existing["holdout_envelope_sha256"] == envelope_sha256
        )
        if same_holdout and existing["candidate_freeze_fingerprint"] != freeze["freeze_fingerprint"]:
            raise ValueError("Parser v5 candidate changed after holdout opening; use a fresh sealed holdout")
        expected = {
            "holdout_id": holdout_id,
            "holdout_envelope_sha256": envelope_sha256,
            "candidate_freeze_fingerprint": freeze["freeze_fingerprint"],
        }
        if any(existing[key] != value for key, value in expected.items()):
            raise ValueError("Parser v5 holdout open record disagrees with requested authorization")
        return existing

    payload = {
        "schema_version": 1,
        "status": "opened",
        "holdout_id": holdout_id,
        "holdout_envelope_sha256": envelope_sha256,
        "holdout_samples_sha256": str(envelope["samples_sha256"]),
        "partition_fingerprint": str(envelope["partition_fingerprint"]),
        "candidate_freeze_fingerprint": str(freeze["freeze_fingerprint"]),
    }
    record = {**payload, "open_fingerprint": _sha256_bytes(_canonical_json(payload))}
    atomic_write(destination, _canonical_json(record) + b"\n")
    return record


__all__ = [
    "authorize_parser_v5_holdout_open",
    "freeze_parser_v5_candidate",
    "load_parser_v5_candidate_freeze",
]