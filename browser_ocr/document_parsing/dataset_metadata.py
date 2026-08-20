from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_FIELDS = {
    "builder",
    "truth_samples_sha256",
    "observation_kind",
    "split",
    "seed",
    "ocr_producer",
    "source_dataset_id",
    "source_manifest_sha256",
    "source_samples_sha256",
}
_SHA_FIELDS = {"truth_samples_sha256", "source_manifest_sha256", "source_samples_sha256"}
_OBSERVATION_KINDS = {"oracle", "synthetic_ocr", "runtime_ocr"}
_SPLITS = {"train", "val", "test", "all"}
_BUILDERS = {"parser_training_builder_v1", "parser_runtime_builder_v2", "real_annotation_finalize_v1"}


def _token(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"parser dataset metadata {label} must be a lowercase ASCII identifier")
    return text


def _sha256(value: object, label: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"parser dataset metadata {label} must be lowercase SHA-256")
    return text


def _runtime_producer(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("parser dataset metadata ocr_producer must be an object")
    from .observation_profile import runtime_observation_producer

    raw = dict(value)
    if "image_sha256" in raw:
        raise ValueError("parser dataset metadata ocr_producer must not include image_sha256")
    probe = {**raw, "image_sha256": "0" * 64}
    normalized = runtime_observation_producer(probe, expected_image_sha256="0" * 64)
    if raw != normalized:
        raise ValueError("parser dataset metadata ocr_producer must use canonical runtime OCR producer fields")
    return normalized


def normalize_parser_metadata(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("parser dataset metadata must be an object")
    unknown = sorted(set(value) - _ALLOWED_FIELDS)
    if unknown:
        raise ValueError(f"unsupported parser dataset metadata fields: {', '.join(map(str, unknown))}")

    normalized: dict[str, Any] = {}
    for field, raw in value.items():
        if field == "builder":
            builder = _token(raw, field)
            if builder not in _BUILDERS:
                raise ValueError("parser dataset metadata builder is unsupported")
            normalized[field] = builder
        elif field == "source_dataset_id":
            normalized[field] = _token(raw, field)
        elif field in _SHA_FIELDS:
            normalized[field] = _sha256(raw, field)
        elif field == "observation_kind":
            if raw not in _OBSERVATION_KINDS:
                raise ValueError("parser dataset metadata observation_kind is unsupported")
            normalized[field] = raw
        elif field == "split":
            if raw not in _SPLITS:
                raise ValueError("parser dataset metadata split is unsupported")
            normalized[field] = raw
        elif field == "seed":
            if isinstance(raw, bool) or not isinstance(raw, int) or not -(2**63) <= raw < 2**63:
                raise ValueError("parser dataset metadata seed must be a signed 64-bit integer")
            normalized[field] = raw
        elif field == "ocr_producer":
            normalized[field] = _runtime_producer(raw)
    return normalized


def validate_parser_metadata_for_documents(
    metadata: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
) -> None:
    if not documents:
        raise ValueError("parser dataset metadata cannot describe an empty document set")

    declared_split = metadata.get("split")
    actual_splits = {str(document.get("split") or "") for document in documents}
    if declared_split is not None and declared_split != "all" and actual_splits != {declared_split}:
        raise ValueError("parser dataset metadata split disagrees with document splits")

    declared_observation = metadata.get("observation_kind")
    actual_observations = {
        str(document.get("observation", {}).get("kind") or "")
        for document in documents
        if isinstance(document.get("observation"), Mapping)
    }
    if declared_observation is not None and actual_observations != {declared_observation}:
        raise ValueError("parser dataset metadata observation_kind disagrees with document observations")

    builder = metadata.get("builder")
    if builder == "parser_training_builder_v1":
        required = {"truth_samples_sha256", "observation_kind", "split", "seed"}
        missing = sorted(required - set(metadata))
        if missing:
            raise ValueError(
                "parser dataset metadata parser_training_builder_v1 is missing fields: " + ", ".join(missing)
            )
        if metadata["observation_kind"] not in {"oracle", "synthetic_ocr"}:
            raise ValueError("parser training builder metadata requires oracle or synthetic_ocr observations")
        if {document.get("source_kind") for document in documents} != {"synthetic"}:
            raise ValueError("parser training builder metadata requires synthetic documents")
        for document in documents:
            observation = document.get("observation")
            profile = observation.get("profile") if isinstance(observation, Mapping) else None
            if not isinstance(profile, Mapping):
                raise ValueError("parser training builder document observation profile is missing")
            if profile.get("truth_samples_sha256") != metadata["truth_samples_sha256"]:
                raise ValueError("parser dataset metadata truth_samples_sha256 disagrees with observation profile")
            if metadata["observation_kind"] == "synthetic_ocr" and profile.get("seed") != metadata["seed"]:
                raise ValueError("parser dataset metadata seed disagrees with synthetic observation profile")
    elif builder == "parser_runtime_builder_v2":
        required = {"truth_samples_sha256", "observation_kind", "split", "ocr_producer"}
        missing = sorted(required - set(metadata))
        if missing:
            raise ValueError(
                "parser dataset metadata parser_runtime_builder_v2 is missing fields: " + ", ".join(missing)
            )
        if metadata["observation_kind"] != "runtime_ocr":
            raise ValueError("parser runtime builder metadata requires runtime_ocr observations")
        if {document.get("source_kind") for document in documents} != {"synthetic"}:
            raise ValueError("parser runtime builder metadata requires synthetic documents")
    elif builder == "real_annotation_finalize_v1":
        required = {"source_dataset_id", "source_manifest_sha256", "source_samples_sha256"}
        missing = sorted(required - set(metadata))
        if missing:
            raise ValueError(
                "parser dataset metadata real_annotation_finalize_v1 is missing fields: " + ", ".join(missing)
            )
        if {document.get("source_kind") for document in documents} != {"real_deidentified"}:
            raise ValueError("real annotation metadata requires real_deidentified documents")
        if actual_observations != {"runtime_ocr"}:
            raise ValueError("real annotation metadata requires runtime_ocr observations")

    producer = metadata.get("ocr_producer")
    if producer is not None:
        from .observation_profile import runtime_observation_producer

        for document in documents:
            observation = document.get("observation")
            if not isinstance(observation, Mapping) or observation.get("kind") != "runtime_ocr":
                raise ValueError("parser dataset metadata ocr_producer requires runtime_ocr documents")
            current = runtime_observation_producer(
                observation.get("profile"),
                expected_image_sha256=str(document.get("image_sha256") or ""),
            )
            if current != producer:
                raise ValueError("parser dataset metadata ocr_producer disagrees with document producer")


__all__ = ["normalize_parser_metadata", "validate_parser_metadata_for_documents"]