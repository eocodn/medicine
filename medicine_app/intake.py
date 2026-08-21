"""Dormant integration contract for future structured medication-intake providers.

The product currently exposes no provider-backed ingestion route. A future learned
parser may live behind this boundary, but it may only hand the product structured
medication rows. Raw images, OCR text, filesystem paths, and other source artifacts
never cross the product boundary, and the provider does not establish canonical
product identity: product_query is resolved by product search and corrected, together
with the draft, only in the final medication editor.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from .prescriptions import normalize_draft


INTAKE_SCHEMA_VERSION = 1
_MAX_ROWS = 24
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ROW_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_ENVELOPE_FIELDS = {"schema_version", "provider_id", "rows"}
_ROW_FIELDS = {"row_id", "product_query", "draft", "uncertainty_codes"}
_DRAFT_FIELDS = {
    "dosage_text",
    "dose_amount",
    "dose_unit",
    "frequency_per_day",
    "meal_relation",
    "administration_route",
    "as_needed",
    "prescription_days",
    "schedule_times",
    "start_date",
    "end_date",
}
_FORBIDDEN_SOURCE_FIELDS = {
    "image",
    "image_uri",
    "image_path",
    "file",
    "file_path",
    "pdf",
    "pdf_uri",
    "raw",
    "raw_text",
    "ocr_text",
    "source_text",
}


class MedicationDraftProvider(Protocol):
    """Interface a future provider must satisfy outside the active product flow."""

    provider_id: str

    def produce(self, source: object) -> Mapping[str, Any]: ...


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    keys = set(value)
    if keys & _FORBIDDEN_SOURCE_FIELDS:
        raise ValueError(f"raw source artifacts are not allowed in {label}")
    unknown = sorted(keys - allowed)
    if unknown:
        raise ValueError(f"unsupported {label} fields: {', '.join(map(str, unknown))}")


def _normalize_row(value: Mapping[str, Any], seen_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("each medication intake row must be an object")
    _reject_unknown(value, _ROW_FIELDS, "medication intake row")
    row_id = str(value.get("row_id") or "").strip()
    if not _ROW_ID_RE.fullmatch(row_id):
        raise ValueError("row_id must use 1-64 ASCII letters, digits, '_' or '-'")
    if row_id in seen_ids:
        raise ValueError("row_id must be unique within an intake envelope")
    seen_ids.add(row_id)
    product_query = str(value.get("product_query") or "").strip()
    if not product_query or len(product_query) > 256:
        raise ValueError("product_query must contain 1-256 characters")
    draft_value = value.get("draft")
    if not isinstance(draft_value, Mapping):
        raise ValueError("draft must be an object")
    _reject_unknown(draft_value, _DRAFT_FIELDS, "medication draft")
    issues = value.get("uncertainty_codes") or []
    if not isinstance(issues, list) or len(issues) > 16:
        raise ValueError("uncertainty_codes must be a list with at most 16 values")
    normalized_issues: list[str] = []
    for issue in issues:
        code = str(issue or "").strip()
        if not _ISSUE_RE.fullmatch(code):
            raise ValueError("uncertainty codes must be uppercase ASCII identifiers")
        if code not in normalized_issues:
            normalized_issues.append(code)
    return {
        "row_id": row_id,
        "product_query": product_query,
        "draft": normalize_draft(dict(draft_value)),
        "uncertainty_codes": normalized_issues,
    }


def normalize_provider_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the provider/product boundary without registering an ingestion path."""
    if not isinstance(value, Mapping):
        raise ValueError("medication intake envelope must be an object")
    _reject_unknown(value, _ENVELOPE_FIELDS, "medication intake envelope")
    if value.get("schema_version") != INTAKE_SCHEMA_VERSION:
        raise ValueError("unsupported medication intake schema version")
    provider_id = str(value.get("provider_id") or "").strip()
    if not _PROVIDER_ID_RE.fullmatch(provider_id):
        raise ValueError("provider_id must be 1-64 ASCII letters, digits, '.', '_' or '-'")
    rows = value.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_ROWS:
        raise ValueError(f"rows must contain between 1 and {_MAX_ROWS} medications")
    seen_ids: set[str] = set()
    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "provider_id": provider_id,
        "rows": [_normalize_row(row, seen_ids) for row in rows],
    }


__all__ = [
    "INTAKE_SCHEMA_VERSION",
    "MedicationDraftProvider",
    "normalize_provider_envelope",
]
