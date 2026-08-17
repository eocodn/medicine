from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .safety import age_years


def _check(
    category: str,
    label: str,
    summary: str,
    *,
    details: str | None,
) -> dict[str, Any]:
    return {
        "category": category,
        "label": label,
        "status": "hit",
        "summary": summary,
        "details": details,
        "findings": [],
    }


def _compact_korean_flag(value: Any) -> str:
    return "".join(str(value or "").split())


def build_product_flag_checks(product: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert product-only DUR flags into explicit display checks.

    These flags are authoritative statements about the product itself. They are
    intentionally separate from patient-profile and quantitative evaluation;
    split-caution is a product handling warning rather than a dose limit.
    """
    checks: list[dict[str, Any]] = []
    for flag in product.get("product_flags") or []:
        category = str(flag.get("category") or "")
        if category == "split_caution":
            details = str(flag.get("details") or "분할 시 주의가 필요한 제품입니다.")
            compact_details = _compact_korean_flag(details)
            if compact_details == "분할가능":
                continue
            checks.append(
                _check(
                    category,
                    "서방정 분할주의",
                    "분할불가" if compact_details == "분할불가" else "분할주의 있음",
                    details=details,
                )
            )
    return checks


def apply_product_flag_fallbacks(
    core_checks: list[dict[str, Any]],
    product: Mapping[str, Any],
    person: Mapping[str, Any],
    *,
    detailed_product_categories: set[str],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Use DUR product-info flags only when detailed product rules are absent.

    B-F flags prove that the product belongs to a DUR caution family, but some
    flags do not carry the threshold needed for a full automatic decision. A
    detailed product-rule result remains authoritative when available; otherwise
    the flag is applied as a fail-closed fallback instead of being ignored.
    """
    result = [dict(item) for item in core_checks]
    by_category = {str(item.get("category") or ""): item for item in result}
    flags = {
        str(flag.get("category") or ""): flag
        for flag in product.get("product_flags") or []
    }
    supported = {
        "age_contraindication",
        "pregnancy_contraindication",
        "dose_caution",
        "duration_caution",
        "elderly_caution",
    }
    for category in supported & flags.keys():
        if category in detailed_product_categories:
            continue
        item = by_category.get(category)
        if item is None or item.get("status") == "hit":
            continue

        if category == "pregnancy_contraindication":
            pregnancy = person.get("pregnancy_status")
            if person.get("sex") == "male" or pregnancy in {"not_pregnant", "not_applicable"}:
                item.update(status="not_applicable", summary="해당사항 없음")
                item.pop("details", None)
            elif pregnancy == "pregnant":
                item.update(
                    status="hit",
                    summary="임부금기 주의사항 있음",
                    details="식약처 DUR 품목정보에서 임부금기 대상 품목으로 분류된 제품입니다.",
                )
            else:
                item.update(
                    status="unknown",
                    summary="임신 여부 확인 필요",
                    details="식약처 DUR 품목정보에서 임부금기 대상 품목으로 분류되어 임신 여부 확인이 필요합니다.",
                )
            continue

        if category == "elderly_caution":
            current_age = age_years(str(person["birth_date"]), as_of)
            if current_age < 65:
                item.update(status="not_applicable", summary="해당사항 없음")
                item.pop("details", None)
            else:
                item.update(
                    status="hit",
                    summary="노인주의 대상",
                    details="식약처 DUR 품목정보에서 노인주의 대상 품목으로 분류된 제품입니다.",
                )
            continue

        if category == "age_contraindication":
            item.update(
                status="unknown",
                summary="특정연령대금기 확인 필요",
                details="식약처 DUR 품목정보에 특정연령대금기 표시가 있으나 적용 연령 상세 기준을 연결하지 못했습니다.",
            )
            continue

        label = "용량주의" if category == "dose_caution" else "투여기간주의"
        item.update(
            status="unknown",
            summary=f"{label} 확인 필요",
            details=f"식약처 DUR 품목정보에 {label} 표시가 있으나 자동 비교에 필요한 상세 기준을 연결하지 못했습니다.",
        )
    return result


__all__ = ["apply_product_flag_fallbacks", "build_product_flag_checks"]
