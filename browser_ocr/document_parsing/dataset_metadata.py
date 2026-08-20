from __future__ import annotations

import re
from typing import Any, Mapping


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


__all__ = ["normalize_parser_metadata"]