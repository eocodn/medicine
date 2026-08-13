from __future__ import annotations

from typing import Any, Mapping


def coverage_summary(
    product: Mapping[str, Any],
    dataset: Mapping[str, Any],
    person: Mapping[str, Any],
    *,
    relevant_profile_categories: set[str] | None = None,
    category_issues: Mapping[str, list[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    product_status = product.get("product_mapping_status") or "not_matched"
    issues = category_issues or {}
    profile_gaps: list[str] = []
    reproductive_applicable = person.get("sex") != "male"
    relevant = relevant_profile_categories
    if reproductive_applicable and person.get("pregnancy_status") == "unknown" and (
        relevant is None or "pregnancy_contraindication" in relevant
    ):
        profile_gaps.append("pregnancy_contraindication")
    if reproductive_applicable and person.get("lactation_status", "unknown") == "unknown" and (
        relevant is None or "lactation_caution" in relevant
    ):
        profile_gaps.append("lactation_caution")

    not_evaluable: list[dict[str, Any]] = []
    if dataset.get("status") != "verified":
        not_evaluable.append({
            "category": "dataset", "result": "not_evaluable",
            "reason": "canonical DUR 데이터셋 검증 상태를 확인하지 못했습니다.",
        })
    if product_status != "matched":
        not_evaluable.append({
            "category": "product_mapping", "result": "not_evaluable",
            "reason": "MFDS ITEM_SEQ 제품을 canonical 데이터에 연결하지 못했습니다.",
        })
    for category, rows in sorted(issues.items()):
        if category == "lactation_caution":
            reason = "수유부주의 성분 적용범위를 canonical 근거로 확정하지 못했습니다."
        else:
            reason = "MFDS ITEM_SEQ DUR 규칙은 있으나 XLSX 상세 기준 연결을 확정하지 못했습니다."
        not_evaluable.append({
            "category": category, "result": "not_evaluable", "reason": reason,
            "source_rows": list(rows),
        })
    for category in profile_gaps:
        reason = (
            "임신 여부가 미확정이라 임부금기 적용 여부를 판정할 수 없습니다."
            if category == "pregnancy_contraindication"
            else "수유 여부가 미입력이라 수유부주의 적용 여부를 판정할 수 없습니다."
        )
        not_evaluable.append({"category": category, "result": "not_evaluable", "reason": reason})
    limited = bool(not_evaluable)
    return {
        "status": "limited" if limited else "complete",
        "message": "일부 항목은 자동으로 확인하지 못했어요." if limited else "현재 프로필과 canonical DUR 범위에서 확인했어요.",
        "dataset": dict(dataset),
        "product": {
            "status": product_status,
            "identity_status": product.get("product_identity_status") or product_status,
            "identity_method": product.get("product_identity_method"),
            "item_seq": product.get("catalog_item_seq"),
            "edi_codes": list(product.get("edi_codes") or []),
        },
        "ingredient": {
            "status": "not_required",
            "mapping_method": "canonical_applicability",
        },
        "category_resolution": {
            category: "unresolved" for category in issues
        },
        "profile": {"not_evaluable_categories": profile_gaps},
        "not_evaluable_checks": not_evaluable,
    }


__all__ = ["coverage_summary"]
