from __future__ import annotations

from typing import Mapping, Any


def require_active_permit(product: Mapping[str, Any]) -> None:
    if product.get("catalog_source") != "canonical":
        return
    if product.get("permit_status") == "active":
        return
    raise ValueError("inactive permit product cannot be added to the current medication regimen")


def dur_review_required(assessment: Mapping[str, Any]) -> bool:
    return any(
        item.get("status") == "unknown"
        for item in assessment.get("dur_checks") or []
    )


def medication_update_values(current: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "dosage_text": current.get("dosage_text"),
        "dose_amount": current.get("dose_amount"),
        "dose_unit": current.get("dose_unit"),
        "frequency_per_day": current.get("frequency_per_day"),
        "meal_relation": current.get("meal_relation"),
        "administration_route": current.get("administration_route"),
        "as_needed": current.get("as_needed"),
        "prn_max_per_day": current.get("prn_max_per_day"),
        "prescription_days": current.get("prescription_days"),
        "long_term": current.get("long_term"),
        "schedule_times": [item["time_of_day"] for item in current.get("schedules") or []],
        "start_date": current.get("start_date"),
        "end_date": current.get("end_date"),
    }
    values.update(changes)
    if changes.get("as_needed") is True:
        # Regimen mode is authoritative: PRN has no fixed daily occurrences.
        if "schedule_times" not in changes:
            values["schedule_times"] = []
        if "frequency_per_day" not in changes:
            values["frequency_per_day"] = None
    elif changes.get("as_needed") is False and "prn_max_per_day" not in changes:
        values["prn_max_per_day"] = None
    if "schedule_times" in changes and "frequency_per_day" not in changes:
        values["frequency_per_day"] = None
    if (
        {"prescription_days", "start_date", "long_term"} & changes.keys()
        and "end_date" not in changes
    ):
        values["end_date"] = None
    return values


def resolve_product(
    products: Any,
    resolved_ref: str | None,
    manual_name: str | None,
    ingredient_name: str | None,
    *,
    canonical_con: Any | None = None,
) -> dict:
    if resolved_ref:
        product = (
            products.get_from_connection(canonical_con, resolved_ref)
            if canonical_con is not None
            else products.get(resolved_ref)
        )
        return {**product, "med_source": "catalog_search"}
    name = (manual_name or "").strip()
    if not name:
        raise ValueError("product_ref, product_code or manual_name is required")
    return {
        "product_ref": None, "catalog_item_seq": None, "product_code": None,
        "product_name": name, "ingredient_code": None, "ingredient_name": ingredient_name,
        "manufacturer": None, "dosage_form": None, "catalog_source": "manual",
        "dur_match": False, "dur_coverage_status": "limited", "med_source": "manual",
    }


__all__ = [
    "dur_review_required", "medication_update_values", "require_active_permit", "resolve_product",
]
