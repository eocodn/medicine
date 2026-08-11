from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from typing import Any, Mapping

from medicine_dur.verification import dataset_manifest

from .coverage import coverage_summary
from .ingredient_safety import collect_ingredient_risks, evaluate_ingredient_duration
from .safety import collect_qualitative_risks, evaluate_quantitative


EVALUATOR_VERSION = "2"


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
        personal_con, person["id"], exclude_id=exclude_medication_id
    )
    current = _current_products(app, current)
    product_risks: list[dict] = []
    ingredient_risks: list[dict] = []
    ingredient_not_evaluable: list[dict] = []
    quantitative = {
        "duration": {"result": "not_evaluable", "reason": "DUR product code is not linked", "source_scope": "product"},
        "dose": {"result": "not_evaluable", "reason": "DUR product code is not linked", "source_scope": "product"},
    }
    with app._dur() as dur_con:
        dataset = dataset_manifest(dur_con)
        if product.get("dur_match"):
            product_risks = collect_qualitative_risks(dur_con, product, person, current, as_of)
            quantitative = evaluate_quantitative(dur_con, product, draft)
            for dimension in quantitative.values():
                dimension.setdefault("source_scope", "product")
        if product.get("ingredient_mapping_status") in {"matched", "partial"}:
            ingredient_risks, ingredient_not_evaluable = collect_ingredient_risks(
                dur_con, product, person, current, as_of
            )
            if quantitative["duration"].get("result") == "not_evaluable":
                ingredient_duration = evaluate_ingredient_duration(dur_con, product, draft)
                if not product.get("dur_match") or ingredient_duration.get("result") in {"within", "exceeded"}:
                    quantitative["duration"] = ingredient_duration

    coverage = coverage_summary(product, dataset, person)
    coverage["not_evaluable_checks"].extend(ingredient_not_evaluable)
    risks = _dedupe_risks(product_risks, ingredient_risks)
    product_status = coverage["product"]["status"]
    ingredient_status = coverage["ingredient"]["status"]
    critical_coverage_gap = (
        (dataset.get("source_count", 0) > 0 and dataset.get("status") != "verified")
        or product_status == "ambiguous"
        or (product_status != "matched" and ingredient_status != "matched")
    )
    requires_review = (
        any(risk.get("severity") in {"danger", "warning"} for risk in risks)
        or any(quantitative[name].get("result") == "exceeded" for name in ("duration", "dose"))
        or critical_coverage_gap
    )
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset": dataset,
        "coverage": coverage,
        "risks": risks,
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
    token = hashlib.sha256(
        f"{EVALUATOR_VERSION}\0{dataset_id}\0{payload_hash}".encode("utf-8")
    ).hexdigest()
    assessment["warning_token"] = token
    return token


def requires_acknowledgement(assessment: Mapping[str, Any]) -> bool:
    return bool(assessment.get("requires_review"))


__all__ = ["assess_medication", "bind_warning_token", "requires_acknowledgement"]
