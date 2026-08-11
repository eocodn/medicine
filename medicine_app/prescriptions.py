from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .safety import APP_TIMEZONE


MEAL_RELATION_VALUES = {
    "unspecified", "before_meal", "after_meal", "with_meal", "empty_stomach", "regardless"
}
ADMINISTRATION_ROUTE_VALUES = {
    "oral", "topical", "inhaled", "ophthalmic", "otic", "nasal", "injection", "other", "unknown"
}


def _normalize_time(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError("schedule time must be HH:MM") from exc
    return parsed.strftime("%H:%M")


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def normalize_draft(values: dict) -> dict:
    schedule_times = [
        _normalize_time(value) for value in values.get("schedule_times") or []
    ]
    if len(schedule_times) != len(set(schedule_times)):
        raise ValueError("schedule_times must not contain duplicates")
    frequency = values.get("frequency_per_day")
    if frequency is None and schedule_times:
        frequency = len(schedule_times)
    amount = values.get("dose_amount")
    if amount is not None:
        try:
            amount_decimal = Decimal(str(amount))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("dose_amount must be a finite number") from exc
        if not amount_decimal.is_finite() or amount_decimal <= 0:
            raise ValueError("dose_amount must be > 0")
        amount = _format_decimal(amount_decimal)
    dose_unit = (values.get("dose_unit") or "").strip() or None
    if frequency is not None:
        try:
            frequency_decimal = Decimal(str(frequency))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("frequency_per_day must be a positive integer") from exc
        if (
            not frequency_decimal.is_finite()
            or frequency_decimal != frequency_decimal.to_integral_value()
            or frequency_decimal < 1
            or frequency_decimal > 24
        ):
            raise ValueError("frequency_per_day must be between 1 and 24")
        frequency = int(frequency_decimal)
        if schedule_times and frequency != len(schedule_times):
            raise ValueError("frequency_per_day must match the number of schedule_times")
    meal_relation = values.get("meal_relation", "unspecified")
    route = values.get("administration_route", "oral")
    if meal_relation not in MEAL_RELATION_VALUES:
        raise ValueError(f"invalid meal_relation: {meal_relation}")
    if route not in ADMINISTRATION_ROUTE_VALUES:
        raise ValueError(f"invalid administration_route: {route}")
    days = values.get("prescription_days")
    if days is not None:
        try:
            days_decimal = Decimal(str(days))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("prescription_days must be a positive integer") from exc
        if (
            not days_decimal.is_finite()
            or days_decimal != days_decimal.to_integral_value()
            or days_decimal < 1
            or days_decimal > 3650
        ):
            raise ValueError("prescription_days must be between 1 and 3650")
        days = int(days_decimal)
    start = date.fromisoformat(values["start_date"]) if values.get("start_date") else datetime.now(APP_TIMEZONE).date()
    finish = date.fromisoformat(values["end_date"]) if values.get("end_date") else None
    if days is not None:
        computed = start + timedelta(days=days - 1)
        if finish is not None and finish != computed:
            raise ValueError("end_date conflicts with start_date and prescription_days")
        finish = computed
    if finish is not None and finish < start:
        raise ValueError("end_date must be on or after start_date")
    dosage_text = values.get("dosage_text")
    if dosage_text is None and amount is not None:
        dosage_text = f"{amount}{dose_unit or ''}"
    return {
        "dosage_text": dosage_text, "dose_amount": amount, "dose_unit": dose_unit,
        "frequency_per_day": frequency, "meal_relation": meal_relation,
        "administration_route": route, "as_needed": bool(values.get("as_needed", False)),
        "prescription_days": days, "schedule_times": schedule_times,
        "start_date": start.isoformat(), "end_date": finish.isoformat() if finish else None,
    }


def draft_hash(person_id: str, product: dict, draft: dict) -> str:
    payload = {"person_id": person_id, "product_ref": product.get("product_ref"), **draft}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
