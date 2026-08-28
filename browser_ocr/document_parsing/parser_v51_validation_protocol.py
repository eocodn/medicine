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
from .parser_v5_training_artifact import resolve_parser_v5_checkpoint
from .parser_v51_runtime_decode import ParserV51RuntimeDecodeConfig


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_IMPLEMENTATION_FILES = (
    "parser_v51_runtime_decode.py",
    "parser_v51_inference_paddle.py",
    "parser_v51_validation_protocol.py",
)
_FREEZE_FIELDS = {
    "schema_version",
    "status",
    "model_id",
    "training_result_sha256",
    "training_profile_sha256",
    "checkpoint_sha256",
    "train_datasets",
    "development_datasets",
    "calibration_fingerprint",
    "calibration_source_fingerprint",
    "runtime_producer_fingerprint",
    "decode_policy",
    "implementation_sha256",
    "freeze_fingerprint",
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
        samples_sha256 = _require_sha(raw.get("samples_sha256"), f"{label} samples_sha256")
        if not dataset_id or (dataset_id, samples_sha256) in seen:
            raise ValueError(f"{label} entries must be unique and named")
        seen.add((dataset_id, samples_sha256))
        identities.append({"dataset_id": dataset_id, "samples_sha256": samples_sha256})
    return sorted(identities, key=lambda item: item["dataset_id"])


def _current_training_implementation(profile: Mapping[str, Any]) -> dict[str, str]:
    expected = profile.get("implementation_sha256")
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError("Parser v5.1 training implementation identity is missing")
    root = Path(__file__).resolve().parent
    values: dict[str, str] = {}
    for raw_name, raw_sha in expected.items():
        name = str(raw_name)
        expected_sha = _require_sha(raw_sha, f"Parser v5.1 training implementation {name}")
        source = root / name
        if not source.is_file() or _sha256_file(source) != expected_sha:
            raise ValueError(f"Parser v5.1 training implementation drifted: {name}")
        values[name] = expected_sha
    return values


def _freeze_implementation(profile: Mapping[str, Any]) -> dict[str, str]:
    root = Path(__file__).resolve().parent
    values = _current_training_implementation(profile)
    for name in _RUNTIME_IMPLEMENTATION_FILES:
        source = root / name
        if not source.is_file():
            raise ValueError(f"Parser v5.1 runtime implementation source is missing: {name}")
        values[name] = _sha256_file(source)
    return dict(sorted(values.items()))


def freeze_parser_v51_candidate(
    *,
    training_result: str | Path,
    development_manifests: Sequence[str | Path],
    calibration_artifact: str | Path,
    output_path: str | Path,
) -> Path:
    result_path = Path(training_result).resolve()
    result = _json_file(result_path, label="Parser v5.1 training result")
    if result.get("schema_version") != 1 or result.get("status") != "ok":
        raise ValueError("Parser v5.1 candidate freeze requires a completed training result")
    profile = result.get("profile")
    if not isinstance(profile, Mapping) or profile.get("model_id") != "parser_v51_direct_rows_v1":
        raise ValueError("Parser v5.1 training profile is invalid")
    profile_sha256 = _sha256_bytes(_canonical_json(profile))
    if result.get("profile_sha256") != profile_sha256:
        raise ValueError("Parser v5.1 training profile SHA-256 mismatch")
    train_datasets = _dataset_identities(profile.get("train_datasets"), "Parser v5.1 freeze train datasets")
    validation_datasets = _dataset_identities(
        profile.get("validation_datasets"), "Parser v5.1 freeze validation datasets"
    )
    development = sorted(
        (
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256}
            for dataset in (load_parser_v5_dataset(path) for path in development_manifests)
        ),
        key=lambda item: item["dataset_id"],
    )
    if development != validation_datasets:
        raise ValueError("Parser v5.1 freeze development datasets disagree with training validation profile")
    checkpoint = resolve_parser_v5_checkpoint(result_path, result.get("best_checkpoint"))
    if not checkpoint.is_file() or _sha256_file(checkpoint) != result.get("best_checkpoint_sha256"):
        raise ValueError("Parser v5.1 freeze best checkpoint identity mismatch")
    calibration = load_parser_v5_calibration(calibration_artifact)
    payload = {
        "schema_version": 1,
        "status": "frozen",
        "model_id": "parser_v51_direct_rows_v1",
        "training_result_sha256": _sha256_file(result_path),
        "training_profile_sha256": profile_sha256,
        "checkpoint_sha256": _sha256_file(checkpoint),
        "train_datasets": train_datasets,
        "development_datasets": development,
        "calibration_fingerprint": str(calibration["calibration_fingerprint"]),
        "calibration_source_fingerprint": str(calibration["source_fingerprint"]),
        "runtime_producer_fingerprint": str(calibration["producer_fingerprint"]),
        "decode_policy": asdict(ParserV51RuntimeDecodeConfig()),
        "implementation_sha256": _freeze_implementation(profile),
    }
    freeze = {**payload, "freeze_fingerprint": _sha256_bytes(_canonical_json(payload))}
    destination = Path(output_path).resolve()
    atomic_write(destination, _canonical_json(freeze) + b"\n")
    return destination


def load_parser_v51_candidate_freeze(path: str | Path) -> dict[str, Any]:
    value = _json_file(path, label="Parser v5.1 candidate freeze")
    if set(value) != _FREEZE_FIELDS or value.get("schema_version") != 1 or value.get("status") != "frozen":
        raise ValueError("Parser v5.1 candidate freeze fields/state are invalid")
    if value.get("model_id") != "parser_v51_direct_rows_v1":
        raise ValueError("Parser v5.1 candidate freeze model identity is invalid")
    for field in (
        "training_result_sha256",
        "training_profile_sha256",
        "checkpoint_sha256",
        "calibration_fingerprint",
        "calibration_source_fingerprint",
        "runtime_producer_fingerprint",
        "freeze_fingerprint",
    ):
        _require_sha(value.get(field), f"Parser v5.1 freeze {field}")
    _dataset_identities(value.get("train_datasets"), "Parser v5.1 freeze train datasets")
    _dataset_identities(value.get("development_datasets"), "Parser v5.1 freeze development datasets")
    decode_policy = value.get("decode_policy")
    if not isinstance(decode_policy, Mapping):
        raise ValueError("Parser v5.1 candidate freeze decode policy is invalid")
    ParserV51RuntimeDecodeConfig(**dict(decode_policy))
    implementation = value.get("implementation_sha256")
    if not isinstance(implementation, Mapping) or not implementation:
        raise ValueError("Parser v5.1 candidate freeze implementation identity is invalid")
    for name, sha in implementation.items():
        _require_sha(sha, f"Parser v5.1 freeze implementation {name}")
    payload = {key: value[key] for key in value if key != "freeze_fingerprint"}
    if _sha256_bytes(_canonical_json(payload)) != value["freeze_fingerprint"]:
        raise ValueError("Parser v5.1 candidate freeze fingerprint mismatch")
    return value


def validate_parser_v51_frozen_implementation(freeze: Mapping[str, Any]) -> None:
    expected = freeze.get("implementation_sha256")
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError("Parser v5.1 frozen implementation identity is invalid")
    root = Path(__file__).resolve().parent
    for raw_name, raw_sha in expected.items():
        name = str(raw_name)
        source = root / name
        if not source.is_file() or _sha256_file(source) != str(raw_sha):
            raise ValueError(f"Parser v5.1 frozen implementation drifted: {name}")


__all__ = [
    "freeze_parser_v51_candidate",
    "load_parser_v51_candidate_freeze",
    "validate_parser_v51_frozen_implementation",
]