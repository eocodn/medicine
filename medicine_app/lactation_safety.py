from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .canonical_runtime import item_seq, lactation_links, lactation_unresolved


def evaluate_lactation_applicability(con: sqlite3.Connection, product: Mapping[str, Any]) -> dict[str, Any]:
    target = item_seq(product)
    if not target:
        return {"result": "not_evaluable", "source_scope": "canonical", "reason": "ITEM_SEQ is unavailable"}
    linked = lactation_links(con, target)
    if linked:
        return {"result": "applicable", "source_scope": "canonical", "source_rows": linked}
    unresolved = lactation_unresolved(con, target)
    if unresolved:
        return {
            "result": "not_evaluable",
            "source_scope": "canonical",
            "reason": "수유부주의 성분 적용범위를 canonical 근거로 확정하지 못했습니다.",
            "source_rows": unresolved,
        }
    return {"result": "not_applicable", "source_scope": "canonical"}


def collect_lactation_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applicability = evaluate_lactation_applicability(con, product)
    if person.get("sex") == "male" or person.get("lactation_status") in {"not_breastfeeding", "not_applicable"}:
        return [], []
    if person.get("lactation_status") != "breastfeeding":
        return [], []
    if applicability["result"] == "not_evaluable":
        return [], [{
            "category": "lactation_caution",
            "result": "not_evaluable",
            "reason": applicability["reason"],
            "source_scope": "canonical",
            "source_rows": applicability.get("source_rows") or [],
        }]
    if applicability["result"] != "applicable":
        return [], []
    risks = []
    for row in applicability.get("source_rows") or []:
        risks.append({
            "type": "lactation_caution",
            "severity": "warning",
            "title": "성분 수유부주의 대상",
            "details": row.get("criterion_details") or row.get("criterion_note"),
            "source_scope": "canonical_ingredient_applicability",
            "dataset_key": row.get("criterion_source_dataset_key"),
            "source_row": row.get("criterion_source_row"),
        })
    return risks, []


__all__ = ["collect_lactation_risks", "evaluate_lactation_applicability"]
