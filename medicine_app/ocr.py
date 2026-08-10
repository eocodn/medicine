"""Strict, transient boundary for structured hints produced by on-device OCR.

The server deliberately accepts hints, rather than OCR output.  This keeps
image bytes, content URIs, local paths, and full recognized text outside the
Python process and outside the personal database.  Review tokens are held in
memory and are bound to the exact normalized draft fingerprint.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .prescriptions import draft_hash


OCR_SCHEMA_VERSION = 1
_BANNED_NAMES = {
    "image", "image_bytes", "raw_text", "text", "uri", "path", "content_uri",
    "local_path", "file_path", "image_uri", "image_path", "source_uri",
}
_ALLOWED_ENVELOPE = {"version", "schema_version", "operation_id", "hints", "fields"}
_ALLOWED_HINTS = {
    "product_ref", "product_code", "product_name", "medicine_name", "product_query",
    "dose", "dose_amount", "dose_unit", "dosage_text", "frequency", "frequency_per_day",
    "days", "prescription_days", "times", "schedule_times", "start_date", "end_date",
    "meal_relation", "administration_route", "as_needed",
}


class OCRValidationError(ValueError):
    """A malformed or unsafe hint envelope, with machine-readable issues."""

    def __init__(self, message: str, issues: list[dict] | None = None):
        self.issues = issues or [{"field": "envelope", "code": "invalid", "message": message}]
        detail = self.issues[0].get("field", "envelope") if self.issues else "envelope"
        super().__init__(f"{message} ({detail})")


def _issue(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def _contains_banned(value: Any, location: str = "envelope") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).strip().lower()
            compact = name.replace("_", "")
            if (
                name in _BANNED_NAMES
                or compact in {"rawtext", "imagebytes", "imagedata", "contenturi", "localpath", "filepath"}
                or "uri" in name
                or name.endswith("path")
            ):
                return f"{location}.{key}"
            found = _contains_banned(child, f"{location}.{key}")
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _contains_banned(child, f"{location}[{index}]")
            if found:
                return found
    return None


def _value(value: Any) -> Any:
    # Android may attach confidence metadata to a scalar.  Confidence is not
    # persisted or trusted; only the structured value crosses this boundary.
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    return value


def _number(value: Any, field: str, issues: list[dict], *, positive: bool = True) -> str | None:
    value = _value(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        issues.append(_issue(field, "invalid_number", "dose must be a finite number"))
        return None
    if not parsed.is_finite() or (positive and parsed <= 0):
        issues.append(_issue(field, "invalid_number", "dose must be greater than zero"))
        return None
    return format(parsed, "f")


def _parse_dose(value: Any, unit: Any, issues: list[dict]) -> tuple[str | None, str | None]:
    value, unit = _value(value), _value(unit)
    if value is None:
        return None, str(unit).strip() if unit is not None and str(unit).strip() else None
    if isinstance(value, (int, float, Decimal)):
        return _number(value, "dose_amount", issues), str(unit).strip() if unit else None
    text = str(value).strip().replace(",", ".")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([\w가-힣μ㎎㎍%]+)?", text)
    if not match:
        issues.append(_issue("dose", "invalid_dose", "dose must be a number with an optional unit"))
        return None, None
    amount = _number(match.group(1), "dose_amount", issues)
    parsed_unit = str(unit).strip() if unit else (match.group(2) or None)
    return amount, parsed_unit


def _integer(value: Any, field: str, issues: list[dict], pattern: str, maximum: int) -> int | None:
    value = _value(value)
    if isinstance(value, str):
        match = re.search(pattern, value.strip(), re.IGNORECASE)
        if match:
            value = next((group for group in match.groups() if group is not None), value.strip())
        else:
            # Korean/Latin OCR commonly emits a bare count with a unit suffix.
            bare = re.fullmatch(r"([0-9]+)\s*(?:회|번|times?)?", value.strip(), re.IGNORECASE)
            value = bare.group(1) if bare else value.strip()
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        issues.append(_issue(field, "invalid_integer", "value must be a positive integer"))
        return None
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or parsed < 1 or parsed > maximum:
        issues.append(_issue(field, "invalid_integer", "value must be a positive integer in the supported range"))
        return None
    return int(parsed)


def _normalize_time(value: Any, field: str, issues: list[dict]) -> str | None:
    value = _value(value)
    if not isinstance(value, str):
        issues.append(_issue(field, "invalid_time", "time must be HH:MM"))
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        issues.append(_issue(field, "invalid_time", "time must be HH:MM"))
        return None
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def normalize_hint_envelope(envelope: Mapping[str, Any]) -> dict:
    """Validate and normalize one operation-scoped structured hint envelope."""
    if not isinstance(envelope, Mapping):
        raise OCRValidationError("OCR envelope must be an object")
    banned = _contains_banned(envelope)
    if banned:
        raise OCRValidationError(
            "OCR envelope contains a forbidden raw artifact field",
            [_issue(banned, "forbidden_field", "image, raw text, URI, and path fields are not accepted")],
        )
    unknown = set(envelope) - _ALLOWED_ENVELOPE
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise OCRValidationError(f"unsupported OCR envelope fields: {names}")
    version = envelope.get("version", envelope.get("schema_version"))
    if version != OCR_SCHEMA_VERSION:
        raise OCRValidationError(f"unsupported OCR envelope version: {version!r}")
    operation_id = envelope.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise OCRValidationError("operation_id is required")
    hints = envelope.get("hints", envelope.get("fields"))
    if not isinstance(hints, Mapping):
        raise OCRValidationError("hints must be an object")
    unknown_hints = set(hints) - _ALLOWED_HINTS
    if unknown_hints:
        names = ", ".join(sorted(map(str, unknown_hints)))
        raise OCRValidationError(f"unsupported OCR hint fields: {names}")

    issues: list[dict] = []
    product_ref = _value(hints.get("product_ref") or hints.get("product_code"))
    product_query = _value(
        hints.get("product_query") or hints.get("product_name") or hints.get("medicine_name")
    )
    if product_ref is not None and (not isinstance(product_ref, str) or not product_ref.strip()):
        issues.append(_issue("product_ref", "invalid_product", "product reference must be non-empty"))
        product_ref = None
    if product_query is not None and (not isinstance(product_query, str) or not product_query.strip()):
        issues.append(_issue("product_query", "invalid_product", "product query must be non-empty"))
        product_query = None
    if product_ref is None and product_query is None:
        issues.append(_issue("product", "missing_product", "product selection is required"))

    dose_amount, dose_unit = _parse_dose(hints.get("dose_amount", hints.get("dose")), hints.get("dose_unit"), issues)
    frequency = _integer(
        hints.get("frequency_per_day", hints.get("frequency")), "frequency_per_day", issues,
        r"(?:하루|일|day(?:s)?)\s*([0-9]+)", 24,
    ) if hints.get("frequency_per_day", hints.get("frequency")) is not None else None
    days = _integer(
        hints.get("prescription_days", hints.get("days")), "prescription_days", issues,
        r"([0-9]+)\s*(?:일|day(?:s)?)", 3650,
    ) if hints.get("prescription_days", hints.get("days")) is not None else None
    times_raw = _value(hints.get("schedule_times", hints.get("times")))
    times: list[str] = []
    if times_raw is not None:
        if isinstance(times_raw, str):
            times_raw = [times_raw]
        if not isinstance(times_raw, (list, tuple)):
            issues.append(_issue("schedule_times", "invalid_time", "schedule_times must be a list"))
        else:
            for index, value in enumerate(times_raw):
                normalized = _normalize_time(value, f"schedule_times[{index}]", issues)
                if normalized is not None:
                    times.append(normalized)
            if len(times) != len(set(times)):
                issues.append(_issue("schedule_times", "duplicate_time", "schedule times must not repeat"))
    if frequency is None and times:
        frequency = len(times)

    draft: dict[str, Any] = {}
    for key, value in (
        ("dose_amount", dose_amount), ("dose_unit", dose_unit), ("frequency_per_day", frequency),
        ("prescription_days", days), ("schedule_times", times), ("start_date", _value(hints.get("start_date"))),
        ("end_date", _value(hints.get("end_date"))), ("meal_relation", _value(hints.get("meal_relation"))),
        ("administration_route", _value(hints.get("administration_route"))),
        ("as_needed", _value(hints.get("as_needed"))),
    ):
        if value is not None:
            draft[key] = value
    for field in ("start_date", "end_date"):
        if field in draft:
            try:
                datetime.strptime(str(draft[field]), "%Y-%m-%d")
            except ValueError:
                issues.append(_issue(field, "invalid_date", "date must be YYYY-MM-DD"))
    return {
        "version": OCR_SCHEMA_VERSION, "schema_version": OCR_SCHEMA_VERSION,
        "operation_id": operation_id.strip(),
        "product_ref": product_ref.strip() if isinstance(product_ref, str) else None,
        "product_query": product_query.strip() if isinstance(product_query, str) else None,
        "draft": draft,
        "issues": issues,
    }


def inspect_envelope(envelope: Mapping[str, Any]) -> dict:
    """Return observable validation output without opening or writing a DB."""
    try:
        result = normalize_hint_envelope(envelope)
    except OCRValidationError as exc:
        return {"valid": False, "issues": exc.issues, "error": str(exc)}
    result["valid"] = not bool(result["issues"])
    return result


# Small public aliases keep the boundary discoverable to bridge and agent
# callers without introducing alternate validation behavior.
validate_envelope = normalize_hint_envelope
normalize_envelope = normalize_hint_envelope
ocr_inspect = inspect_envelope


def load_envelope(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OCRValidationError("OCR input is not valid JSON", [_issue("input", "invalid_json", "input is not valid JSON")]) from exc
    return inspect_envelope(value)


def _review_fingerprint(person_id: str, product: Mapping[str, Any], draft: Mapping[str, Any]) -> str:
    """Canonicalize JSON number spellings before binding a token."""
    normalized = dict(draft)
    if normalized.get("dose_amount") is not None:
        try:
            amount = format(Decimal(str(normalized["dose_amount"])), "f").rstrip("0").rstrip(".")
            normalized["dose_amount"] = amount or "0"
            if normalized.get("dose_unit"):
                normalized["dosage_text"] = f"{amount}{normalized['dose_unit']}"
        except (InvalidOperation, ValueError):
            pass
    return draft_hash(person_id, dict(product), normalized)


class OCRReviewStore:
    """Process-local review tokens; no OCR material is retained or serialized."""

    def __init__(self, *, clock=None, ttl_seconds: float = 300.0, max_tokens: int = 256) -> None:
        if ttl_seconds <= 0:
            raise ValueError("OCR review token TTL must be positive")
        if max_tokens < 1:
            raise ValueError("OCR review token capacity must be positive")
        self._lock = threading.RLock()
        self._clock = clock or time.monotonic
        self._ttl_seconds = float(ttl_seconds)
        self._max_tokens = int(max_tokens)
        self._tokens: dict[str, tuple[str, str, str, float]] = {}

    def _cleanup_locked(self, now: float) -> None:
        expired = [token for token, record in self._tokens.items() if record[3] <= now]
        for token in expired:
            self._tokens.pop(token, None)

    @property
    def size(self) -> int:
        with self._lock:
            self._cleanup_locked(self._clock())
            return len(self._tokens)

    def issue(self, person_id: str, product_ref: str, draft_fingerprint: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._cleanup_locked(now)
            while len(self._tokens) >= self._max_tokens:
                self._tokens.pop(next(iter(self._tokens)))
            self._tokens[token] = (person_id, product_ref, draft_fingerprint, now + self._ttl_seconds)
        return token

    def verify(self, token: str | None, person_id: str, product_ref: str, draft_fingerprint: str) -> bool:
        if not token:
            return False
        with self._lock:
            self._cleanup_locked(self._clock())
            record = self._tokens.get(token)
            expected = (person_id, product_ref, draft_fingerprint)
            if record is None:
                return False
            if record[:3] != expected:
                # A failed binding attempt consumes the token.  This prevents a
                # changed draft from falling back to the previously reviewed one.
                self._tokens.pop(token, None)
                return False
            return True

    def invalidate(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._tokens.pop(token, None)


def split_ocr_request(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], str | None]:
    """Extract an optional wrapper while rejecting every unrecognized sibling."""
    if not isinstance(payload, Mapping):
        raise OCRValidationError("OCR preview body must be an object")
    banned = _contains_banned(payload)
    if banned:
        raise OCRValidationError(
            "OCR preview body contains a forbidden raw artifact field",
            [_issue(banned, "forbidden_field", "image, raw text, URI, and path fields are not accepted")],
        )
    wrappers = {"envelope", "hint_envelope", "ocr_envelope"}
    present = wrappers.intersection(payload)
    if not present:
        normalize_hint_envelope(payload)
        return payload, None
    allowed = wrappers | {"product_ref"}
    unknown = set(payload) - allowed
    if unknown:
        raise OCRValidationError(f"unsupported OCR preview fields: {', '.join(sorted(map(str, unknown)))}")
    selected_name = next(iter(present))
    selected = payload[selected_name]
    if not isinstance(selected, Mapping):
        raise OCRValidationError("OCR preview envelope must be an object")
    normalize_hint_envelope(selected)
    sibling_ref = payload.get("product_ref")
    return selected, sibling_ref if isinstance(sibling_ref, str) else None


def preview_ocr(service: Any, person_id: str, envelope: Mapping[str, Any], product_ref: str | None = None) -> dict:
    """Resolve active candidates and then delegate the selected draft to the normal preview path."""
    normalized = normalize_hint_envelope(envelope)
    candidates: list[dict] = []
    selected_ref = product_ref or normalized["product_ref"]
    query = selected_ref or normalized["product_query"]
    if query:
        candidates = service.search_products(query, limit=30, include_inactive=False)
    if selected_ref:
        candidates = [item for item in candidates if item.get("product_ref") == selected_ref]
        if not candidates:
            normalized["issues"].append(_issue("product_ref", "not_active_or_found", "an active catalog product must be selected"))
    elif len(candidates) == 1:
        selected_ref = candidates[0]["product_ref"]
    elif len(candidates) > 1:
        normalized["issues"].append(_issue("product", "candidate_selection_required", "select one active catalog candidate"))
    if not selected_ref:
        normalized["issues"].append(_issue("product", "candidate_required", "an active catalog product is required"))
    draft = dict(normalized["draft"])
    if draft.get("dose_amount") is None:
        normalized["issues"].append(_issue("dose_amount", "missing_dose", "dose amount is required for OCR review"))
    if draft.get("frequency_per_day") is None:
        normalized["issues"].append(_issue("frequency_per_day", "missing_frequency", "frequency is required for OCR review"))
    if draft.get("prescription_days") is None:
        normalized["issues"].append(_issue("prescription_days", "missing_days", "prescription days are required for OCR review"))
    result: dict = {
        "version": normalized["version"], "operation_id": normalized["operation_id"],
        "candidates": candidates, "issues": normalized["issues"], "draft": draft,
        "ocr_review_token": None,
    }
    if normalized["issues"]:
        return result
    result = service.preview_medication(person_id, {"product_ref": selected_ref, **draft})
    result["candidates"] = candidates
    result["issues"] = []
    result["ocr_operation_id"] = normalized["operation_id"]
    result["draft_fingerprint"] = _review_fingerprint(person_id, candidates[0], result["draft"])
    result["ocr_review_token"] = service.ocr_reviews.issue(
        person_id, selected_ref, result["draft_fingerprint"]
    )
    return result


def validate_ocr_create(
    service: Any, token: str | None, origin: bool, person_id: str, product: Mapping[str, Any],
    draft: Mapping[str, Any], request_id: str | None,
) -> None:
    if not (token or origin):
        return
    if not request_id:
        raise ValueError("OCR-origin create requires request_id")
    product_ref = product.get("product_ref") or product.get("catalog_item_seq") or ""
    fingerprint = _review_fingerprint(person_id, product, draft)
    if not service.ocr_reviews.verify(token, person_id, str(product_ref), fingerprint):
        raise ValueError("ocr_review_token does not match the reviewed draft")
