from __future__ import annotations

from typing import Any, Mapping

from .interaction_timing import courses_overlap
from .safety import age_years


DUR_CATEGORIES = (
    ("combination_contraindication", "병용금기"),
    ("age_contraindication", "연령금기"),
    ("pregnancy_contraindication", "임부금기"),
    ("elderly_caution", "노인주의"),
    ("dose_caution", "용량주의"),
    ("duration_caution", "투여기간주의"),
    ("therapeutic_duplication_caution", "효능군 중복주의"),
)

_INTERACTION_CATEGORIES = {
    "combination_contraindication",
    "therapeutic_duplication_caution",
}


def _item(
    category: str,
    label: str,
    status: str,
    summary: str,
    *,
    details: str | None = None,
    findings: list[dict[str, Any]] | None = None,
    qualifiers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "category": category,
        "label": label,
        "status": status,
        "summary": summary,
        "findings": findings or [],
    }
    if details:
        result["details"] = details
    if qualifiers:
        result["qualifiers"] = qualifiers
    return result


def _profile_not_applicable(
    category: str,
    person: Mapping[str, Any],
    *,
    current_age: int,
) -> bool:
    if category == "pregnancy_contraindication":
        return person.get("sex") == "male" or person.get("pregnancy_status") in {
            "not_pregnant", "not_applicable"
        }
    if category == "elderly_caution":
        return current_age < 65
    return False


def _mapping_complete(category: str, coverage: Mapping[str, Any]) -> bool:
    product_status = (coverage.get("product") or {}).get("status")
    unresolved = coverage.get("category_resolution") or {}
    return product_status == "matched" and unresolved.get(category) != "unresolved"


def _mapping_reason(category: str, coverage: Mapping[str, Any]) -> str:
    unresolved = coverage.get("category_resolution") or {}
    if unresolved.get(category) == "unresolved":
        return "DUR 상세 기준을 자동으로 하나로 확정하지 못했습니다. 의사 또는 약사에게 확인하세요."
    if (coverage.get("product") or {}).get("status") != "matched":
        return "MFDS ITEM_SEQ 제품을 canonical 데이터에 연결하지 못했습니다."
    return "DUR 판정 범위를 완전히 확인하지 못했습니다. 의사 또는 약사에게 확인하세요."


def _current_mapping_issues(
    current: list[Mapping[str, Any]],
    candidate_course: Mapping[str, Any],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for medication in current:
        product_matched = medication.get("product_mapping_status") == "matched"
        canonical_issues = medication.get("canonical_resolution_issues") or {}
        if product_matched and not canonical_issues:
            continue
        if courses_overlap(medication, candidate_course) is False:
            continue
        scope = "canonical DUR 상세 기준" if product_matched else "MFDS ITEM_SEQ"
        issues.append({
            "name": str(medication.get("product_name") or "이름을 확인할 수 없는 복용약"),
            "scope": scope,
        })
    return issues


def _current_mapping_issue_text(
    issues: list[dict[str, str]],
    category_label: str,
) -> tuple[str, str]:
    if len(issues) == 1:
        issue = issues[0]
        name = issue["name"]
        scope = issue["scope"]
        return (
            f"{name} 확인 필요",
            f"{name}의 {scope} DUR 연결을 확인하지 못해 {category_label}를 완전히 비교하지 못했습니다.",
        )
    named_issues = ", ".join(
        f"{issue['name']} ({issue['scope']})"
        for issue in issues
    )
    return (
        f"현재 복용약 {len(issues)}개 확인 필요",
        f"DUR 연결을 확인하지 못한 현재 복용약: {named_issues}. {category_label}를 완전히 비교하지 못했습니다.",
    )


def _category_issue(category: str, coverage: Mapping[str, Any]) -> str | None:
    for issue in coverage.get("not_evaluable_checks") or []:
        if issue.get("category") == category:
            return str(issue.get("reason") or "자동 판정하지 못했습니다.")
    return None


def _friendly_quantitative_reason(category: str, check: Mapping[str, Any]) -> str:
    reason = str(check.get("reason") or "")
    if check.get("pediatric_review"):
        return "소아 용량은 나이·체중·적응증에 따른 별도 처방 기준 확인이 필요합니다."
    if category == "duration_caution" and reason == "prescription duration is missing or invalid":
        return "처방 일수를 입력하면 투여기간 기준을 확인할 수 있습니다."
    if category == "dose_caution" and (
        "dose input is missing" in reason or "daily frequency is missing" in reason
    ):
        return "1회 복용량과 1일 횟수를 입력하면 용량 기준을 확인할 수 있습니다."
    if "dosage form" in reason:
        return "제품 제형을 확정하지 못해 자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요."
    if reason:
        return "DUR 기준을 하나의 조건으로 확정하지 못해 자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요."
    return "자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요."


def _quantitative_item(
    category: str,
    label: str,
    check: Mapping[str, Any],
    *,
    mapping_complete: bool,
    mapping_reason: str,
    dataset_verified: bool,
) -> dict[str, Any]:
    result = check.get("result")
    qualifiers = list(check.get("qualifiers") or [])
    if result == "exceeded":
        requested = check.get("requested_days", check.get("daily_amount"))
        maximum = check.get("maximum_days", check.get("maximum_daily_amount"))
        unit = check.get("unit") or ("일" if category == "duration_caution" else "")
        details = None
        if requested is not None and maximum is not None:
            if category == "dose_caution":
                details = f"입력한 1일 용량 {requested}{unit} · DUR 기준 {maximum}{unit}"
            else:
                details = f"입력한 투여기간 {requested}{unit} · DUR 기준 {maximum}{unit}"
        return _item(
            category, label, "hit", f"{label} 기준 초과",
            details=details, qualifiers=qualifiers,
        )
    if not dataset_verified:
        return _item(
            category, label, "unknown", "자동 확인 제한",
            details="필수 DUR 원본을 검증하지 못해 확인할 수 없습니다.",
            qualifiers=qualifiers,
        )
    if result == "not_evaluable":
        status = "conditional" if check.get("evaluation_status") == "conditional" else "unknown"
        summary = f"{label} 조건 확인 필요" if status == "conditional" else "확인 필요"
        return _item(
            category, label, status, summary,
            details=_friendly_quantitative_reason(category, check),
            qualifiers=qualifiers,
        )
    if not mapping_complete:
        return _item(
            category, label, "unknown", "자동 확인 제한",
            details=mapping_reason, qualifiers=qualifiers,
        )
    if result == "within":
        requested = check.get("requested_days", check.get("daily_amount"))
        maximum = check.get("maximum_days", check.get("maximum_daily_amount"))
        unit = check.get("unit") or ("일" if category == "duration_caution" else "")
        details = None
        if requested is not None and maximum is not None:
            details = f"입력값 {requested}{unit} · 기준 {maximum}{unit}"
        return _item(
            category, label, "clear", "기준 이내",
            details=details, qualifiers=qualifiers,
        )
    if result == "not_applicable":
        return _item(
            category, label, "not_applicable", "해당 기준 없음", qualifiers=qualifiers
        )
    return _item(
        category, label, "unknown", "확인 필요",
        details="자동 판정 결과를 확정하지 못했습니다.", qualifiers=qualifiers,
    )


def build_dur_checks(
    *,
    person: Mapping[str, Any],
    current: list[Mapping[str, Any]],
    risks: list[dict[str, Any]],
    duration: Mapping[str, Any],
    dose: Mapping[str, Any],
    coverage: Mapping[str, Any],
    dataset: Mapping[str, Any],
    candidate_course: Mapping[str, Any],
    as_of=None,
) -> list[dict[str, Any]]:
    """Return one authoritative display state for each supported DUR family."""
    by_category: dict[str, list[dict[str, Any]]] = {}
    for risk in risks:
        category = str(risk.get("type") or "")
        by_category.setdefault(category, []).append(risk)

    current_age = age_years(str(person["birth_date"]), as_of)
    dataset_verified = dataset.get("status") == "verified"
    current_mapping_issues = _current_mapping_issues(current, candidate_course)
    result: list[dict[str, Any]] = []

    for category, label in DUR_CATEGORIES:
        findings = by_category.get(category, [])
        hit_findings = [
            finding for finding in findings
            if finding.get("severity") in {"danger", "warning"}
            and finding.get("evaluation_status") not in {"unknown", "conditional"}
        ]
        conditional_findings = [
            finding for finding in findings
            if finding.get("evaluation_status") == "conditional"
        ]
        unresolved_findings = [
            finding for finding in findings
            if finding not in hit_findings and finding not in conditional_findings
        ]

        if hit_findings:
            first = hit_findings[0]
            result.append(_item(
                category,
                label,
                "hit",
                str(first.get("title") or f"{label} 주의사항 있음"),
                details=str(first.get("details") or "") or None,
                findings=findings,
            ))
            continue

        if _profile_not_applicable(category, person, current_age=current_age):
            result.append(_item(category, label, "not_applicable", "해당사항 없음"))
            continue

        if conditional_findings:
            first = conditional_findings[0]
            result.append(_item(
                category,
                label,
                "conditional",
                str(first.get("title") or f"{label} 조건 확인 필요"),
                details=str(first.get("details") or "규칙의 적용 조건을 확인해야 합니다."),
                findings=conditional_findings,
            ))
            continue

        if category == "dose_caution":
            result.append(_quantitative_item(
                category,
                label,
                dose,
                mapping_complete=_mapping_complete(category, coverage),
                mapping_reason=_mapping_reason(category, coverage),
                dataset_verified=dataset_verified,
            ))
            continue
        if category == "duration_caution":
            result.append(_quantitative_item(
                category,
                label,
                duration,
                mapping_complete=_mapping_complete(category, coverage),
                mapping_reason=_mapping_reason(category, coverage),
                dataset_verified=dataset_verified,
            ))
            continue

        if unresolved_findings:
            first = unresolved_findings[0]
            result.append(_item(
                category,
                label,
                "unknown",
                "확인 필요",
                details=str(first.get("details") or first.get("title") or "자동 판정하지 못했습니다."),
                findings=findings,
            ))
            continue

        issue = _category_issue(category, coverage)
        if issue:
            result.append(_item(category, label, "unknown", "확인 필요", details=issue))
            continue
        if not dataset_verified:
            result.append(_item(
                category, label, "unknown", "자동 확인 제한",
                details="필수 DUR 원본을 검증하지 못해 확인할 수 없습니다.",
            ))
            continue
        if not _mapping_complete(category, coverage):
            result.append(_item(
                category, label, "unknown", "자동 확인 제한",
                details=_mapping_reason(category, coverage),
            ))
            continue
        if category in _INTERACTION_CATEGORIES and current_mapping_issues:
            summary, details = _current_mapping_issue_text(current_mapping_issues, label)
            result.append(_item(
                category,
                label,
                "unknown",
                summary,
                details=details,
            ))
            continue

        clear_summary = {
            "combination_contraindication": "병용금기 없음",
            "age_contraindication": "연령금기 해당 없음",
            "pregnancy_contraindication": "임부금기 해당 없음",
            "elderly_caution": "노인주의 해당 없음",
            "therapeutic_duplication_caution": "중복 없음",
        }[category]
        result.append(_item(category, label, "clear", clear_summary))

    return result


__all__ = ["DUR_CATEGORIES", "build_dur_checks"]
