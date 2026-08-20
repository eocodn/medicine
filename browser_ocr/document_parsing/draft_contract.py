from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Mapping

from .contract import DRAFT_FIELDS


_MEAL_RELATIONS = {
    "unspecified",
    "before_meal",
    "after_meal",
    "with_meal",
    "empty_stomach",
    "regardless",
}
_ADMINISTRATION_ROUTES = {
    "oral",
    "topical",
    "inhaled",
    "ophthalmic",
    "otic",
    "nasal",
    "injection",
    "other",
    "unknown",
}
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    text = value.strip()
    if not text or len(text) > maximum or any(char in text for char in "\r\n\x00"):
        raise ValueError(f"{field} must be a non-empty single-line string up to {maximum} characters")
    return text


def _positive_number(value: object, field: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return value


def _positive_integer(value: object, field: str, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be an integer between 1 and {maximum}")
    return value


def _enum(value: object, field: str, allowed: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} has an unsupported value")
    return value


def _date(value: object, field: str) -> str | None:
    text = _optional_text(value, field, 10)
    if text is None:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must use YYYY-MM-DD")
    return text


def _schedule_times(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("schedule_times must be a list with at most 24 HH:MM values")
    times: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not _TIME_RE.fullmatch(raw):
            raise ValueError("schedule_times values must use HH:MM")
        if raw in times:
            raise ValueError("schedule_times must not contain duplicates")
        times.append(raw)
    return times


def normalize_parser_draft(value: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(value) - DRAFT_FIELDS)
    if unknown:
        raise ValueError(f"unsupported draft fields: {', '.join(unknown)}")
    normalized: dict[str, Any] = {}
    for field, raw in value.items():
        if field == "dosage_text":
            normalized[field] = _optional_text(raw, field, 256)
        elif field == "dose_amount":
            normalized[field] = _positive_number(raw, field)
        elif field == "dose_unit":
            normalized[field] = _optional_text(raw, field, 64)
        elif field == "frequency_per_day":
            normalized[field] = _positive_integer(raw, field, 24)
        elif field == "meal_relation":
            normalized[field] = _enum(raw, field, _MEAL_RELATIONS)
        elif field == "administration_route":
            normalized[field] = _enum(raw, field, _ADMINISTRATION_ROUTES)
        elif field == "as_needed":
            if raw is not None and not isinstance(raw, bool):
                raise ValueError("as_needed must be boolean or null")
            normalized[field] = raw
        elif field == "prescription_days":
            normalized[field] = _positive_integer(raw, field, 3650)
        elif field == "schedule_times":
            normalized[field] = _schedule_times(raw)
        elif field in {"start_date", "end_date"}:
            normalized[field] = _date(raw, field)
    return normalized


__all__ = ["normalize_parser_draft"]