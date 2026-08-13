from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any, Mapping

from .canonical_coverage import coverage_summary
from .canonical_runtime import (
    canonical_manifest, category_resolution_issues, item_seq, lactation_links,
    lactation_unresolved, linked_categories, unlinked_product_rules,
)
from .canonical_safety import collect_qualitative_risks, evaluate_quantitative
from .dur_status import build_dur_checks
from .lactation_safety import collect_lactation_risks
from .product_flags import apply_product_flag_fallbacks, build_product_flag_checks
from .safety import age_years


EVALUATOR_VERSION = "7-canonical"


def _fallback_product(medication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(medication),
        "safety_ingredients": [],
        "ingredient_mapping_status": "not_required",
        "ingredient_mapping_method": "canonical_applicability",
        "product_mapping_status": "not_matched",
        "product_identity_status": "not_matched",
        "product_identity_method": None,
        "matched_product_codes": [],
        "edi_codes": [],
        "canonical_resolution_issues": {},
    }


def _current_products(app: Any, medications: list[dict]) -> list[dict]:
    result = []
    for medication in medications:
        ref = medication.get("catalog_item_seq")
        if not ref:
            result.append(_fallback_product(medication))
            continue
        try:
            product = app.get_product(str(ref))
        except (KeyError, FileNotFoundError, sqlite3.DatabaseError):
            result.append(_fallback_product(medication))
        else:
            result.append({**medication, **product, "id": medication["id"]})
    return result

def _profile_rule_categories(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
) -> set[str]:
    target = item_seq(product)
    if not target:
        return set()
    categories = linked_categories(con, target)
    categories.update(str(row["category"]) for row in unlinked_product_rules(con, target))
    categories.update(str(flag.get("category")) for flag in product.get("product_flags") or [])
    relevant: set[str] = set()
    if person.get("pregnancy_status") == "unknown" and "pregnancy_contraindication" in categories:
        relevant.add("pregnancy_contraindication")
    if person.get("lactation_status", "unknown") == "unknown" and (
        lactation_links(con, target) or lactation_unresolved(con, target)
    ):
        relevant.add("lactation_caution")
    return relevant


def _dedupe_risks(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for group in groups:
        for item in group:
            key = (item.get("type"), item.get("related_medication_id"), item.get("title"), item.get("source_row"))
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(result, key=lambda item: (order.get(item.get("severity"), 9), item.get("title") or ""))

def assess_medication(
    app: Any,
    personal_con: sqlite3.Connection,
    person: dict,
    product: dict,
    draft: dict,
    acknowledged: bool,
    *,
    exclude_medication_id: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    current = app._list_medications_from_connection(
        personal_con, person["id"], active_only=False, exclude_id=exclude_medication_id
    )
    current = _current_products(app, current)
    with app._canonical() as canonical_con:
        dataset = canonical_manifest(canonical_con)
        product_risks = collect_qualitative_risks(
            canonical_con, product, person, current, as_of, candidate_course=draft
        )
        lactation_risks, lactation_not_evaluable = collect_lactation_risks(
            canonical_con, product, person
        )
        quantitative = evaluate_quantitative(canonical_con, product, draft)
        pediatric = age_years(person["birth_date"], as_of) < 19
        if pediatric and quantitative["dose"].get("result") in {"within", "not_applicable"}:
            quantitative["dose"] = {
                "result": "not_evaluable",
                "reason": "adult dose-caution threshold is not a pediatric dose criterion",
                "source_scope": "profile", "pediatric_review": True,
            }
        elif pediatric:
            quantitative["dose"]["pediatric_review"] = True

        target = item_seq(product)
        issues = category_resolution_issues(canonical_con, target) if target else {}
        relevant_profile_categories = _profile_rule_categories(canonical_con, product, person)
        coverage = coverage_summary(
            product, dataset, person,
            relevant_profile_categories=relevant_profile_categories,
            category_issues=issues,
        )
        for issue in lactation_not_evaluable:
            if not any(
                existing.get("category") == issue.get("category")
                for existing in coverage["not_evaluable_checks"]
            ):
                coverage["not_evaluable_checks"].append(issue)
        detailed_product_categories = linked_categories(canonical_con, target) if target else set()

    risks = _dedupe_risks(product_risks, lactation_risks)
    dur_checks = build_dur_checks(
        person=person,
        current=current,
        risks=risks,
        duration=quantitative["duration"],
        dose=quantitative["dose"],
        coverage=coverage,
        dataset=dataset,
        candidate_course=draft,
        as_of=as_of,
    )
    dur_checks = apply_product_flag_fallbacks(
        dur_checks,
        product,
        person,
        detailed_product_categories=detailed_product_categories,
        as_of=as_of,
    )
    dur_checks.extend(build_product_flag_checks(product))
    requires_review = any(item.get("status") in {"hit", "unknown"} for item in dur_checks)
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset": dataset,
        "coverage": coverage,
        "risks": risks,
        "dur_checks": dur_checks,
        "duration": quantitative["duration"],
        "dose": quantitative["dose"],
        "requires_review": requires_review,
        "acknowledged": bool(acknowledged),
    }


def bind_warning_token(assessment: dict[str, Any], payload_hash: str) -> str | None:
    assessment["draft_fingerprint"] = payload_hash
    if not assessment.get("requires_review"):
        assessment["warning_token"] = None
        return None
    dataset_id = assessment.get("dataset", {}).get("dataset_id") or "dataset:unverified"
    reviewed_safety_context = {
        "coverage": assessment.get("coverage"),
        "risks": assessment.get("risks"),
        "duration": assessment.get("duration"),
        "dose": assessment.get("dose"),
        "dur_checks": assessment.get("dur_checks"),
        "requires_review": bool(assessment.get("requires_review")),
    }
    context_hash = hashlib.sha256(
        json.dumps(
            reviewed_safety_context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    token = hashlib.sha256(
        f"{EVALUATOR_VERSION}\0{dataset_id}\0{payload_hash}\0{context_hash}".encode("utf-8")
    ).hexdigest()
    assessment["warning_token"] = token
    return token


def requires_acknowledgement(assessment: Mapping[str, Any]) -> bool:
    return bool(assessment.get("requires_review"))


def has_dur_alert(assessment: Mapping[str, Any]) -> bool:
    """Return whether an assessment contains an actual current DUR finding.

    Generic review-only states such as pediatric dosing uncertainty or incomplete
    coverage are intentionally excluded: the persistent medication-list marker
    means that a DUR danger/warning matched or a quantitative DUR limit was
    exceeded, not merely that the evaluator could not conclude something.
    """
    dur_checks = assessment.get("dur_checks") or []
    if dur_checks:
        return any(item.get("status") == "hit" for item in dur_checks)
    return (
        any(
            risk.get("severity") in {"danger", "warning"}
            and risk.get("evaluation_status") != "unknown"
            for risk in assessment.get("risks") or []
        )
        or any(
            (assessment.get(name) or {}).get("result") == "exceeded"
            for name in ("duration", "dose")
        )
    )


def assess_current_medication(
    app: Any,
    personal_con: sqlite3.Connection,
    person: dict,
    medication: Mapping[str, Any],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Re-evaluate a stored medication without mutating its revision history."""
    ref = medication.get("catalog_item_seq")
    if ref:
        try:
            product = app.get_product(str(ref))
        except (KeyError, FileNotFoundError, sqlite3.DatabaseError):
            product = _fallback_product(medication)
    else:
        product = _fallback_product(medication)
    draft = {
        "dosage_text": medication.get("dosage_text"),
        "dose_amount": medication.get("dose_amount"),
        "dose_unit": medication.get("dose_unit"),
        "frequency_per_day": medication.get("frequency_per_day"),
        "meal_relation": medication.get("meal_relation") or "unspecified",
        "administration_route": medication.get("administration_route") or "unknown",
        "as_needed": bool(medication.get("as_needed")),
        "prescription_days": medication.get("prescription_days"),
        "schedule_times": [
            item["time_of_day"] for item in medication.get("schedules") or []
        ],
        "start_date": medication.get("start_date"),
        "end_date": medication.get("end_date"),
    }
    return assess_medication(
        app,
        personal_con,
        person,
        product,
        draft,
        False,
        exclude_medication_id=str(medication["id"]),
        as_of=as_of,
    )


__all__ = [
    "assess_current_medication",
    "assess_medication",
    "bind_warning_token",
    "has_dur_alert",
    "requires_acknowledgement",
]
