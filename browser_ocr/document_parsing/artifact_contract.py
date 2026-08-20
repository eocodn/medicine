from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .artifact_invariants import validate_parser_document_set
from .dataset_metadata import validate_parser_metadata_for_documents


_STATE_FIELDS = {"schema_version", "status", "profile"}
_STATE_PROFILE_FIELDS = {"schema_version", "dataset_id", "samples_sha256", "metadata_sha256"}


def strict_json_loads(text: str, *, label: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} is not strict JSON: unsupported constant {value}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc


def validate_parser_artifact(
    documents: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> None:
    if not documents:
        raise ValueError("parser dataset must contain at least one document")
    validate_parser_document_set(documents)
    validate_parser_metadata_for_documents(metadata, documents)


def dataset_state_profile(
    *,
    dataset_id: str,
    samples_sha256: str,
    metadata_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "samples_sha256": samples_sha256,
        "metadata_sha256": metadata_sha256,
    }


def normalize_dataset_state(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _STATE_FIELDS:
        raise ValueError("parser dataset output state must contain schema_version, status and profile")
    if value.get("schema_version") != 1:
        raise ValueError("parser dataset output state schema_version must be 1")
    status = value.get("status")
    if status not in {"running", "completed"}:
        raise ValueError("parser dataset output state has unsupported status")
    profile = value.get("profile")
    if not isinstance(profile, Mapping) or set(profile) != _STATE_PROFILE_FIELDS:
        raise ValueError("parser dataset output state profile is invalid")
    if profile.get("schema_version") != 1:
        raise ValueError("parser dataset output state profile schema_version must be 1")
    return {"schema_version": 1, "status": status, "profile": dict(profile)}


def require_completed_dataset_state(value: object, *, expected_profile: Mapping[str, Any]) -> None:
    state = normalize_dataset_state(value)
    if state["status"] != "completed":
        raise ValueError("parser dataset output state is not completed")
    if state["profile"] != dict(expected_profile):
        raise ValueError("parser dataset output state profile disagrees with persisted dataset")


__all__ = [
    "dataset_state_profile",
    "normalize_dataset_state",
    "require_completed_dataset_state",
    "strict_json_loads",
    "validate_parser_artifact",
]