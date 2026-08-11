from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any, Mapping

from medicine_dur.verification import dataset_manifest

from .coverage import coverage_summary
from .dur_status import build_dur_checks
from .ingredient_safety import (
    collect_ingredient_risks,
    evaluate_ingredient_duration,
    evaluate_ingredient_rule_applicability,
)
from .safety import age_years, collect_qualitative_risks, evaluate_quantitative


EVALUATOR_VERSION = "5"


def _coverage_only(reason: str, *, scope: str = "coverage") -> dict[str, Any]:
    return {"result": "not_evaluable", "reason": reason, "source_scope": scope, "coverage_only": True}


def _profile_rule_categories(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
) -> set[str]:
    categories: set[str] = set()
    code = product.get("product_code") or product.get("edi_code")
    if person.get("pregnancy_status") == "unknown":
        product_match = bool(code and con.execute(
            "SELECT 1 FROM product_dur WHERE product_code=? AND category='pregnancy_contraindication' LIMIT 1",
            (code,),
        ).fetchone())
        ingredient = evaluate_ingredient_rule_applicability(con, product, "pregnancy_contraindication")
        ingredient_match = (
            ingredient.get("result") in {"applicable", "not_evaluable"}
            and bool(ingredient.get("source_rows"))
        )
        if product_match or ingredient_match:
            categories.add("pregnancy_contraindication")
    if person.get("lactation_status", "unknown") == "unknown":
        ingredient = evaluate_ingredient_rule_applicability(con, product, "lactation_caution")
        if (
            ingredient.get("result") in {"applicable", "not_evaluable"}
            and ingredient.get("source_rows")
        ):
            categories.add("lactation_caution")
    return categories


def _ingredient_presence_result(applicability: dict[str, Any], category: str) -> dict[str, Any]:
    if applicability["result"] != "applicable":
        return applicability
    rows = applicability.get("source_rows") or []
    result = {
        "result": "not_evaluable",
        "reason": f"ingredient {category} rule is present but has no unambiguous product threshold",
        "source_scope": "ingredient",
        "source_rows": rows,
    }
    if len(rows) == 1:
        result["source"] = rows[0]
    return result


def _fallback_product(medication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(medication),
        "safety_ingredients": [],
        "ingredient_mapping_status": "not_evaluable",
        "ingredient_mapping_reason": "저장된 과거 복용약을 현재 식약처/DUR 제품에 다시 연결하지 못했습니다.",
        "product_mapping_status": "not_matched",
        "matched_product_codes": [],
        "edi_codes": [medication["product_code"]] if medication.get("product_code") else [],
    }


def _current_products(app: Any, medications: list[dict]) -> list[dict]:
    result: list[dict] = []
    for medication in medications:
        ref = medication.get("catalog_item_seq") or medication.get("product_code")
        if not ref:
            result.append(_fallback_product(medication))
            continue
        try:
            product = app.get_product(ref)
        except (KeyError, FileNotFoundError, sqlite3.DatabaseError):
            result.append(_fallback_product(medication))
        else:
            result.append({**medication, **product, "id": medication["id"]})
    return result


def _dedupe_risks(product_risks: list[dict], ingredient_risks: list[dict]) -> list[dict]:
    risks: list[dict] = []
    seen: set[tuple[Any, ...]] = set()
    product_scopes: set[tuple[Any, ...]] = set()
    for item in product_risks:
        enriched = {**item, "source_scope": item.get("source_scope") or "product"}
        key = (enriched.get("type"), enriched.get("related_medication_id"), enriched.get("title"))
        seen.add(key)
        product_scopes.add((enriched.get("type"), enriched.get("related_medication_id")))
        risks.append(enriched)
    for item in ingredient_risks:
        if (item.get("type"), item.get("related_medication_id")) in product_scopes:
            continue
        key = (item.get("type"), item.get("related_medication_id"), item.get("title"))
        if key not in seen:
            seen.add(key)
            risks.append(item)
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(risks, key=lambda item: (order.get(item.get("severity"), 9), item.get("title") or ""))


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
    product_risks: list[dict] = []
    ingredient_risks: list[dict] = []
    ingredient_not_evaluable: list[dict] = []
    quantitative = {
        "duration": _coverage_only("DUR product code is not linked", scope="product"),
        "dose": _coverage_only("DUR product code is not linked", scope="product"),
    }
    relevant_profile_categories: set[str] = set()
    with app._dur() as dur_con:
        dataset = dataset_manifest(dur_con)
        if product.get("dur_match"):
            product_risks = collect_qualitative_risks(
                dur_con, product, person, current, as_of, candidate_course=draft
            )
            quantitative = evaluate_quantitative(dur_con, product, draft)
            for dimension in quantitative.values():
                dimension.setdefault("source_scope", "product")
        ingredient_status = product.get("ingredient_mapping_status")
        if ingredient_status in {"matched", "partial"}:
            ingredient_risks, ingredient_not_evaluable = collect_ingredient_risks(
                dur_con, product, person, current, as_of, candidate_course=draft
            )
            product_duration_result = quantitative["duration"].get("result")
            if product_duration_result in {"not_evaluable", "not_applicable"}:
                ingredient_duration = evaluate_ingredient_duration(dur_con, product, draft)
                ingredient_result = ingredient_duration.get("result")
                if ingredient_status == "matched" and (
                    not product.get("dur_match") or product_duration_result == "not_applicable"
                ):
                    quantitative["duration"] = ingredient_duration
                elif ingredient_result == "exceeded":
                    quantitative["duration"] = ingredient_duration
                elif ingredient_result == "within" and product.get("dur_match"):
                    quantitative["duration"] = ingredient_duration

            product_dose_result = quantitative["dose"].get("result")
            if not product.get("dur_match") or product_dose_result == "not_applicable":
                ingredient_dose = evaluate_ingredient_rule_applicability(dur_con, product, "dose_caution")
                if ingredient_status == "matched":
                    quantitative["dose"] = _ingredient_presence_result(ingredient_dose, "dose_caution")
                elif ingredient_dose.get("result") == "applicable":
                    quantitative["dose"] = _ingredient_presence_result(ingredient_dose, "dose_caution")

        for name in ("duration", "dose"):
            if quantitative[name].get("result") == "not_applicable" and ingredient_status != "matched":
                quantitative[name] = _coverage_only(
                    "authoritative ingredient mapping is unavailable", scope="ingredient"
                )

        pediatric = age_years(person["birth_date"], as_of) < 19
        if pediatric and quantitative["dose"].get("result") in {"within", "not_applicable"}:
            quantitative["dose"] = {
                "result": "not_evaluable",
                "reason": "adult dose-caution threshold is not a pediatric dose criterion",
                "source_scope": "profile",
                "pediatric_review": True,
            }
        elif pediatric:
            quantitative["dose"]["pediatric_review"] = True
        relevant_profile_categories = _profile_rule_categories(dur_con, product, person)

        if dataset.get("status") != "verified":
            for name in ("duration", "dose"):
                if quantitative[name].get("result") == "not_applicable":
                    quantitative[name] = _coverage_only(
                        "DUR dataset is not verified", scope="dataset"
                    )

    coverage = coverage_summary(
        product,
        dataset,
        person,
        relevant_profile_categories=relevant_profile_categories,
    )
    coverage["not_evaluable_checks"].extend(ingredient_not_evaluable)
    risks = _dedupe_risks(product_risks, ingredient_risks)
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
    # The eight-category status model is authoritative for acknowledgement.
    # A definite hit and an unresolved check both require one explicit review;
    # clear and not_applicable are the only non-blocking states.
    requires_review = any(
        item.get("status") in {"hit", "unknown"}
        for item in dur_checks
    )
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
    ref = medication.get("catalog_item_seq") or medication.get("product_code")
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
        "administration_route": medication.get("administration_route") or "oral",
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
