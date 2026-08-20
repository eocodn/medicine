from __future__ import annotations

from typing import Any, Mapping

from .training_dataset import ParserDatasetError


_SHA_FIELDS = {
    "image_sha256",
    "baseline_result_sha256",
    "recognizer_checkpoint_sha256",
    "recognizer_config_sha256",
    "detector_manifest_sha256",
    "detector_asset_sha256",
}
_IMPLEMENTATION_SHA_FIELDS = {
    "full_document",
    "full_document_cli",
    "crop_refinement",
    "detector_runtime",
    "detector_benchmark",
}


def _require_sha256(value: object, label: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ParserDatasetError(f"runtime OCR profile {label} must be lowercase SHA-256")
    return text


def runtime_observation_profile(raw: object, *, expected_image_sha256: str | None = None) -> dict[str, Any]:
    """Keep only detector/recognizer/orchestration identity for parser observations.

    A full-document result also pins the currently selected parser. Parser datasets
    must not inherit that identity: changing the parser must not invalidate an OCR
    observation produced by the same detector, cropper and recognizer.
    """

    if not isinstance(raw, Mapping):
        raise ParserDatasetError("runtime result profile must be an object")
    profile = {str(key): value for key, value in raw.items() if key != "parser"}
    if profile.get("schema_version") != 2:
        raise ParserDatasetError("runtime OCR profile schema_version must be 2")
    for field in _SHA_FIELDS:
        profile[field] = _require_sha256(profile.get(field), field)
    if expected_image_sha256 is not None and profile["image_sha256"] != expected_image_sha256:
        raise ParserDatasetError("runtime OCR profile image SHA-256 does not match document image")
    if profile.get("recognizer_device") not in {"cpu", "gpu"}:
        raise ParserDatasetError("runtime OCR profile recognizer_device must be cpu or gpu")
    detector_model = str(profile.get("detector_model") or "").strip()
    if not detector_model:
        raise ParserDatasetError("runtime OCR profile detector_model is required")
    profile["detector_model"] = detector_model
    for field in ("detector_edge", "detector_threads"):
        value = profile.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ParserDatasetError(f"runtime OCR profile {field} must be a positive integer")
    implementation = profile.get("implementation")
    if not isinstance(implementation, Mapping):
        raise ParserDatasetError("runtime OCR profile implementation must be an object")
    normalized_implementation = {
        str(key): value
        for key, value in implementation.items()
        if key not in {"parser", "parser_contract"}
    }
    for field in _IMPLEMENTATION_SHA_FIELDS:
        normalized_implementation[field] = _require_sha256(normalized_implementation.get(field), f"implementation.{field}")
    profile["implementation"] = normalized_implementation
    return profile


def runtime_observation_producer(raw: object, *, expected_image_sha256: str | None = None) -> dict[str, Any]:
    profile = runtime_observation_profile(raw, expected_image_sha256=expected_image_sha256)
    profile.pop("image_sha256", None)
    return profile


__all__ = ["runtime_observation_producer", "runtime_observation_profile"]
