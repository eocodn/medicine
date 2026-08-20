from __future__ import annotations

import re
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SYNTHETIC_FIELDS = {"kind", "truth_samples_sha256"}
_REAL_FIELDS = {"kind", "source_dataset_id", "source_manifest_sha256", "source_samples_sha256"}


def _sha256(value: object, label: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return text


def _token(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase ASCII identifier")
    return text


def normalize_source_binding(value: object, *, source_kind: str, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    binding = dict(value)
    if source_kind == "synthetic":
        if set(binding) != _SYNTHETIC_FIELDS or binding.get("kind") != "synthetic_truth":
            raise ValueError(f"{label} must contain synthetic truth identity")
        return {
            "kind": "synthetic_truth",
            "truth_samples_sha256": _sha256(binding.get("truth_samples_sha256"), f"{label}.truth_samples_sha256"),
        }
    if source_kind == "real_deidentified":
        if set(binding) != _REAL_FIELDS or binding.get("kind") != "real_source":
            raise ValueError(f"{label} must contain real source identity")
        return {
            "kind": "real_source",
            "source_dataset_id": _token(binding.get("source_dataset_id"), f"{label}.source_dataset_id"),
            "source_manifest_sha256": _sha256(binding.get("source_manifest_sha256"), f"{label}.source_manifest_sha256"),
            "source_samples_sha256": _sha256(binding.get("source_samples_sha256"), f"{label}.source_samples_sha256"),
        }
    raise ValueError(f"{label} source_kind is unsupported")


__all__ = ["normalize_source_binding"]
