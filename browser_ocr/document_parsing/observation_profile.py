from __future__ import annotations

import re

from typing import Any, Mapping

from .training_dataset import ParserDatasetError


_SHA_FIELDS = {
    "image_sha256",
    "recognizer_result_sha256",
    "recognizer_checkpoint_sha256",
    "recognizer_config_sha256",
    "detector_manifest_sha256",
    "detector_asset_sha256",
    "detector_onnx_sha256",
    "detector_config_sha256",
    "inference_runtime_sha256",
    "paddleocr_source_sha256",
    "paddleocr_dictionary_sha256",
}
_IMPLEMENTATION_SHA_FIELDS = {
    "full_document",
    "full_document_cli",
    "full_document_runtime",
    "recognizer_runtime",
    "crop_refinement",
    "orientation",
    "orientation_runtime",
    "detector_runtime",
    "detector_benchmark",
}
_PROFILE_FIELDS = {
    "schema_version",
    *_SHA_FIELDS,
    "recognizer_device",
    "detector_model",
    "detector_edge",
    "detector_threads",
    "implementation",
}
_RAW_PROFILE_FIELDS = _PROFILE_FIELDS
_RAW_IMPLEMENTATION_FIELDS = _IMPLEMENTATION_SHA_FIELDS
_ALLOWED_DETECTOR_MODELS = {"PP-OCRv5_mobile_det", "PP-OCRv6_tiny_det", "PP-OCRv6_small_det"}
_TRAINED_DETECTOR_MODEL = re.compile(r"PP-OCRv5_mobile_det_candidate_[0-9a-f]{12}")
_ORACLE_FIELDS = {"producer", "truth_samples_sha256"}
_SYNTHETIC_FIELDS = {"producer", "revision", "seed", "truth_samples_sha256"}


def _require_sha256(value: object, label: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ParserDatasetError(f"runtime OCR profile {label} must be lowercase SHA-256")
    return text


def runtime_observation_profile(raw: object, *, expected_image_sha256: str | None = None) -> dict[str, Any]:
    """Validate detector/recognizer/orchestration identity for parser observations."""

    if not isinstance(raw, Mapping):
        raise ParserDatasetError("runtime result profile must be an object")
    unknown = sorted(set(raw) - _RAW_PROFILE_FIELDS)
    if unknown:
        raise ParserDatasetError(f"unsupported runtime OCR profile fields: {', '.join(map(str, unknown))}")
    profile = {str(key): value for key, value in raw.items()}
    if profile.get("schema_version") != 3:
        raise ParserDatasetError("runtime OCR profile schema_version must be 3")
    for field in _SHA_FIELDS:
        profile[field] = _require_sha256(profile.get(field), field)
    if expected_image_sha256 is not None and profile["image_sha256"] != expected_image_sha256:
        raise ParserDatasetError("runtime OCR profile image SHA-256 does not match document image")
    if profile.get("recognizer_device") not in {"cpu", "gpu"}:
        raise ParserDatasetError("runtime OCR profile recognizer_device must be cpu or gpu")
    detector_model = str(profile.get("detector_model") or "").strip()
    if detector_model not in _ALLOWED_DETECTOR_MODELS and _TRAINED_DETECTOR_MODEL.fullmatch(detector_model) is None:
        raise ParserDatasetError("runtime OCR profile detector_model must be a supported detector id")
    profile["detector_model"] = detector_model
    for field in ("detector_edge", "detector_threads"):
        value = profile.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ParserDatasetError(f"runtime OCR profile {field} must be a positive integer")
    implementation = profile.get("implementation")
    if not isinstance(implementation, Mapping):
        raise ParserDatasetError("runtime OCR profile implementation must be an object")
    unknown_implementation = sorted(set(implementation) - _RAW_IMPLEMENTATION_FIELDS)
    if unknown_implementation:
        raise ParserDatasetError(
            "unsupported runtime OCR implementation fields: "
            + ", ".join(map(str, unknown_implementation))
        )
    normalized_implementation = {str(key): value for key, value in implementation.items()}
    for field in _IMPLEMENTATION_SHA_FIELDS:
        normalized_implementation[field] = _require_sha256(normalized_implementation.get(field), f"implementation.{field}")
    profile["implementation"] = normalized_implementation
    return profile


def runtime_observation_producer(raw: object, *, expected_image_sha256: str | None = None) -> dict[str, Any]:
    profile = runtime_observation_profile(raw, expected_image_sha256=expected_image_sha256)
    profile.pop("image_sha256", None)
    return profile


def parser_observation_profile(
    kind: str,
    raw: object,
    *,
    expected_image_sha256: str | None = None,
) -> dict[str, Any]:
    if kind == "runtime_ocr":
        return runtime_observation_profile(raw, expected_image_sha256=expected_image_sha256)
    if not isinstance(raw, Mapping):
        raise ParserDatasetError("parser observation profile must be an object")
    profile = dict(raw)
    if kind == "oracle":
        if set(profile) != _ORACLE_FIELDS:
            raise ParserDatasetError("oracle observation profile has unsupported or missing fields")
        if profile.get("producer") != "unified_truth":
            raise ParserDatasetError("oracle observation profile producer must be unified_truth")
        profile["truth_samples_sha256"] = _require_sha256(
            profile.get("truth_samples_sha256"), "truth_samples_sha256"
        )
        return profile
    if kind == "synthetic_ocr":
        if set(profile) != _SYNTHETIC_FIELDS:
            raise ParserDatasetError("synthetic_ocr observation profile has unsupported or missing fields")
        if profile.get("producer") != "deterministic_synthetic_ocr":
            raise ParserDatasetError(
                "synthetic_ocr observation profile producer must be deterministic_synthetic_ocr"
            )
        revision = profile.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 2**31 - 1:
            raise ParserDatasetError("synthetic_ocr observation profile revision must be a positive integer")
        seed = profile.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int) or not -(2**63) <= seed < 2**63:
            raise ParserDatasetError("synthetic_ocr observation profile seed must be a signed 64-bit integer")
        profile["truth_samples_sha256"] = _require_sha256(
            profile.get("truth_samples_sha256"), "truth_samples_sha256"
        )
        return profile
    raise ParserDatasetError("parser observation profile kind is unsupported")


__all__ = ["parser_observation_profile", "runtime_observation_producer", "runtime_observation_profile"]
