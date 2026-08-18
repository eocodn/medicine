from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from medicine_canonical.mfds_scope_overrides import (
    mfds_product_scope_allows,
    mfds_product_scope_is_explicit,
)

from .canonical_runtime import has_unlinked_product_rule, item_seq, linked_product_rows
from .interaction_timing import courses_overlap, interaction_timing_applies, parse_interaction_timing
from .safety import (
    _COUNT_UNITS, _draft_quantity, _frequency, _source_quantity,
    age_rule_evaluation, age_years,
)


def _combination_rows(con: sqlite3.Connection, left: str, right: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT * FROM product_rule_criteria
           WHERE category='combination_contraindication'
             AND ((item_seq=? AND paired_item_seq=?) OR (item_seq=? AND paired_item_seq=?))""",
        (left, right, right, left),
    ).fetchall()
    return [dict(row) for row in rows]

def _unlinked_combination_exists(con: sqlite3.Connection, left: str, right: str) -> bool:
    return con.execute(
        """SELECT 1 FROM product_rules r
           LEFT JOIN product_criterion_links l ON l.product_rule_id=r.id
           WHERE r.category='combination_contraindication'
             AND ((r.item_seq=? AND r.paired_item_seq=?) OR (r.item_seq=? AND r.paired_item_seq=?))
           GROUP BY r.id HAVING COUNT(l.criterion_rule_id)=0 LIMIT 1""",
        (left, right, right, left),
    ).fetchone() is not None


def _canonical_details(row: Mapping[str, Any]) -> str | None:
    return row.get("product_details") or row.get("criterion_details")


def _canonical_ingredient(row: Mapping[str, Any]) -> str | None:
    return row.get("criterion_ingredient_name") or row.get("ingredient_name")


def _canonical_paired_ingredient(row: Mapping[str, Any]) -> str | None:
    return row.get("criterion_paired_ingredient_name") or row.get("paired_ingredient_name")


def _mfds_criterion_note_requires_review(row: Mapping[str, Any]) -> bool:
    """Keep MFDS REMARK qualifiers fail-closed unless an exact reviewed scope override resolves them."""
    dataset_key = str(row.get("criterion_source_dataset_key") or "")
    if not dataset_key.startswith("mfds_dur_ingredient:"):
        return False
    if not str(row.get("criterion_qualifier_note") or "").strip():
        return False
    category = str(row.get("category") or "")
    ingredient_code = row.get("ingredient_code")
    if mfds_product_scope_is_explicit(category, ingredient_code):
        return not mfds_product_scope_allows(
            category, ingredient_code, row.get("item_seq"), row.get("criterion_rule_value")
        )
    return True

def _details_with_professional_review(details: Any) -> str:
    advice = "세부 적용 조건이 있어 의사 또는 약사에게 확인하세요."
    text = str(details or "").strip()
    return f"{text} {advice}" if text else advice


def _pregnancy_note_condition(note: Any) -> str | None:
    text = str(note or "").strip()
    if not text:
        return None
    if re.search(r"(?:단\s*,?|제외|경우|한함|사용\s*시|투여\s*시)", text):
        return text
    return None


def _pregnancy_rule_display(value: Any, note: Any = None) -> str:
    text = str(value or "").strip()
    if text in {"1", "2"}:
        text = f"{text}등급"
    display = text or "등급 미표기"
    condition = _pregnancy_note_condition(note)
    if condition and condition not in display:
        return f"{display} ({condition})"
    return display


def _pregnancy_rule_is_conditional(value: Any, note: Any = None) -> bool:
    """Return whether the DUR grade itself carries an applicability qualifier.

    Canonical pregnancy criteria use plain ``1등급``/``2등급`` for unconditional
    grades and parenthesized text for form, indication, duration, gestational-age,
    or explicit exception conditions. Those conditions are authoritative evidence,
    but the current medication/profile inputs do not model every required clinical
    fact, so they must stay review-required without being presented as a definite hit.
    """
    text = str(value or "").strip()
    return bool(re.search(r"[12]\s*등급\s*\(", text)) or _pregnancy_note_condition(note) is not None


_DURATION_LIMIT_RE = re.compile(r"^\s*(?P<amount>\d+)\s*(?P<unit>일|주|개월)?\s*$")


def _duration_limit_days(value: Any) -> tuple[int | None, str | None]:
    text = str(value or "").strip()
    match = _DURATION_LIMIT_RE.fullmatch(text)
    if not match:
        return None, "duration rule is missing, malformed, or ambiguous"
    amount = int(match.group("amount"))
    if amount <= 0:
        return None, "duration rule is missing, malformed, or ambiguous"
    unit = match.group("unit") or "일"
    if unit == "일":
        return amount, None
    if unit == "주":
        return amount * 7, None
    return None, "month-based duration rule requires calendar-aware evaluation"

def _combination_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    current: list[dict],
    candidate_course: Mapping[str, Any],
) -> list[dict[str, Any]]:
    target = item_seq(product)
    if not target:
        return []
    risks: list[dict[str, Any]] = []
    for medication in current:
        paired = item_seq(medication)
        if not paired:
            continue
        rows = _combination_rows(con, target, paired)
        for row in rows:
            candidate_side = "left" if row.get("item_seq") == target else "right"
            details = _canonical_details(row)
            qualifier_review = _mfds_criterion_note_requires_review(row)
            timing = parse_interaction_timing(
                details or "", _canonical_ingredient(row), _canonical_paired_ingredient(row)
            )
            # MFDS REMARK is preserved as a qualifier but is never interpreted as
            # free-text executable logic. Missing structured inputs therefore stay
            # review-required rather than silently widening the contraindication.
            if qualifier_review:
                details = _details_with_professional_review(details)
            if not interaction_timing_applies(
                timing, candidate_course, medication, candidate_side=candidate_side
            ):
                continue
            message = details or "DUR 병용금기 조합에 해당합니다."
            if timing.get("status") == "not_evaluable":
                message += " 병용금기 규칙은 확인되지만 복용 간격 조건의 적용 여부를 추가로 확인해야 합니다."
            finding = {
                "type": "combination_contraindication", "severity": "danger",
                "title": f"{medication['product_name']}와 병용금기", "details": message,
                "related_medication_id": medication["id"], "timing": timing,
                "source_scope": "canonical_product",
            }
            # The canonical note can carry a dose, indication, age, emergency, or
            # formulation qualifier. Known timing notes are evaluated above; any
            # other non-empty qualifier must remain conditional rather than being
            # presented as an unconditional contraindication.
            if timing.get("status") == "not_evaluable" or qualifier_review:
                finding["evaluation_status"] = "conditional"
            risks.append(finding)
        if not rows and _unlinked_combination_exists(con, target, paired):
            risks.append({
                "type": "combination_contraindication", "severity": "info",
                "title": f"{medication['product_name']}와 병용금기 기준 확인 필요",
                "details": "MFDS ITEM_SEQ 병용금기 규칙은 있으나 상세 기준 연결을 확정하지 못했습니다. 의사 또는 약사에게 확인하세요.",
                "related_medication_id": medication["id"],
                "evaluation_status": "unknown", "source_scope": "canonical_product",
            })
    return risks


def _profile_evaluation_dates(
    candidate_course: Mapping[str, Any] | None,
    as_of: date | None,
) -> list[date | None]:
    course = candidate_course or {}
    try:
        start = date.fromisoformat(str(course["start_date"])) if course.get("start_date") else None
        end = date.fromisoformat(str(course["end_date"])) if course.get("end_date") else None
    except (TypeError, ValueError):
        start, end = None, None
    if as_of is not None:
        first = start if start is not None and as_of < start else as_of
    else:
        first = start
    dates: list[date | None] = [first]
    if end is not None and (first is None or end > first):
        dates.append(end)
    return dates


def _person_specific_risks(
    con: sqlite3.Connection,
    person: Mapping[str, Any],
    product: Mapping[str, Any],
    as_of: date | None = None,
    candidate_course: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target = item_seq(product)
    if not target:
        return []
    rows: list[dict[str, Any]] = []
    for category in ("age_contraindication", "pregnancy_contraindication", "elderly_caution"):
        rows.extend(linked_product_rows(con, target, category))
    risks: list[dict[str, Any]] = []
    evaluation_dates = _profile_evaluation_dates(candidate_course, as_of)
    age_note_review_required = any(
        _mfds_criterion_note_requires_review(row)
        for row in rows
        if row.get("category") == "age_contraindication"
    )
    pregnancy_rows = [row for row in rows if row.get("category") == "pregnancy_contraindication"]
    pregnancy_note_review_required = any(
        _mfds_criterion_note_requires_review(row) for row in pregnancy_rows
    )
    pregnancy_grades = {
        match.group(1)
        for row in pregnancy_rows
        if (match := re.search(r"([12])\s*등급", str(row.get("criterion_rule_value") or "")))
    }
    conflicting_pregnancy_grades = (
        person.get("pregnancy_status") == "pregnant" and len(pregnancy_grades) > 1
    )
    if conflicting_pregnancy_grades:
        first = pregnancy_rows[0]
        risks.append({
            "type": "pregnancy_contraindication",
            "severity": "info",
            "title": "임부금기 기준 확인 필요",
            "details": "서로 다른 임부금기 등급이 함께 적용되어 자동 판정하지 않습니다. 의사 또는 약사에게 확인하세요.",
            "evaluation_status": "unknown",
            "source_scope": "canonical_product",
            "dataset_key": first.get("criterion_source_dataset_key"),
            "source_row": first.get("criterion_source_row"),
        })
    for row in rows:
        category = str(row["category"])
        rule_value = row.get("criterion_rule_value")
        if category == "age_contraindication":
            evaluations = [
                age_rule_evaluation(
                    person["birth_date"], rule_value,
                    row.get("product_dosage_form") or product.get("dosage_form"),
                    evaluation_date,
                )
                for evaluation_date in evaluation_dates
            ]
            if any(applies is True for applies, _ in evaluations):
                title, severity = f"연령금기 · {rule_value}", "danger"
            elif any(applies is None for applies, _ in evaluations):
                reason = next((reason for applies, reason in evaluations if applies is None and reason), None)
                risks.append({
                    "type": category,
                    "severity": "info",
                    "title": "연령금기 기준 확인 필요",
                    "details": reason or "연령금기 기준을 자동 판정하지 못했습니다.",
                    "evaluation_status": "unknown",
                    "source_scope": "canonical_product",
                    "dataset_key": row.get("criterion_source_dataset_key"),
                    "source_row": row.get("criterion_source_row"),
                })
                continue
            else:
                continue
        elif category == "pregnancy_contraindication":
            if person.get("pregnancy_status") != "pregnant":
                continue
            if conflicting_pregnancy_grades:
                continue
            rule_display = _pregnancy_rule_display(rule_value)
            title, severity = f"임부금기 · {rule_display}", "danger"
        else:
            if not any(age_years(person["birth_date"], evaluation_date) >= 65 for evaluation_date in evaluation_dates):
                continue
            title, severity = "노인주의 대상", "warning"
        finding = {
            "type": category, "severity": severity, "title": title,
            "details": _canonical_details(row), "source_scope": "canonical_product",
            "dataset_key": row.get("criterion_source_dataset_key"),
            "source_row": row.get("criterion_source_row"),
        }
        mfds_note_review = (
            (category == "age_contraindication" and age_note_review_required)
            or (category == "pregnancy_contraindication" and pregnancy_note_review_required)
            or (category == "elderly_caution" and _mfds_criterion_note_requires_review(row))
        )
        if mfds_note_review:
            finding["evaluation_status"] = "conditional"
            finding["details"] = _details_with_professional_review(finding.get("details"))
        elif category == "pregnancy_contraindication" and _pregnancy_rule_is_conditional(rule_value):
            finding["evaluation_status"] = "conditional"
        risks.append(finding)
    return risks


def _duplication_groups(
    con: sqlite3.Connection, product: Mapping[str, Any]
) -> dict[str, bool]:
    target = item_seq(product)
    if not target:
        return {}
    groups: dict[str, bool] = {}
    for row in linked_product_rows(con, target, "therapeutic_duplication_caution"):
        value = row.get("criterion_rule_value") or row.get("effect_name")
        if value:
            group = str(value)
            groups[group] = groups.get(group, False) or _mfds_criterion_note_requires_review(row)
    return groups

def _duplication_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    current: list[dict],
    candidate_course: Mapping[str, Any],
) -> list[dict[str, Any]]:
    new_groups = _duplication_groups(con, product)
    risks: list[dict[str, Any]] = []
    for medication in current:
        if courses_overlap(candidate_course, medication) is False:
            continue
        current_groups = _duplication_groups(con, medication)
        for group in sorted(set(new_groups) & set(current_groups)):
            details = f"현재 복용 중인 {medication['product_name']}와 같은 효능군입니다."
            finding = {
                "type": "therapeutic_duplication_caution", "severity": "warning",
                "title": f"효능군 중복주의 · {group}",
                "details": details,
                "related_medication_id": medication["id"], "source_scope": "canonical_product",
            }
            if new_groups[group] or current_groups[group]:
                finding["evaluation_status"] = "conditional"
                finding["details"] = _details_with_professional_review(details)
            risks.append(finding)
    return risks


def collect_qualitative_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
    current: list[dict],
    as_of: date | None = None,
    candidate_course: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    course = candidate_course or {}
    risks = (
        _combination_risks(con, product, current, course)
        + _person_specific_risks(con, person, product, as_of, candidate_course=course)
        + _duplication_risks(con, product, current, course)
    )
    seen = set()
    unique = []
    for risk in risks:
        key = (risk.get("type"), risk.get("title"), risk.get("details"), risk.get("related_medication_id"))
        if key not in seen:
            seen.add(key)
            unique.append(risk)
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(unique, key=lambda risk: (order.get(risk.get("severity"), 9), risk.get("title") or ""))

def _dimension_source(result: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    result["source_rows"] = rows
    if len(rows) == 1:
        result["source"] = rows[0]


def evaluate_quantitative(con: sqlite3.Connection, product: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    target = item_seq(product)
    duration_rows = linked_product_rows(con, target, "duration_caution") if target else []
    duration: dict[str, Any] = {"result": "not_evaluable", "source_scope": "canonical_product"}
    raw_days = draft.get("prescription_days")
    prescription_days: int | None = None
    if raw_days is not None and not isinstance(raw_days, bool):
        try:
            parsed = Decimal(str(raw_days).strip())
            if parsed.is_finite() and parsed > 0 and parsed == parsed.to_integral_value():
                prescription_days = int(parsed)
        except (InvalidOperation, AttributeError, ValueError):
            pass
    distinct_days: set[int] = set()
    duration_parse_reasons: set[str] = set()
    for row in duration_rows:
        maximum_days, reason = _duration_limit_days(row.get("rule_value"))
        if reason:
            duration_parse_reasons.add(reason)
        elif maximum_days is not None:
            distinct_days.add(maximum_days)
    duration_qualifier_review = any(
        _mfds_criterion_note_requires_review(row) for row in duration_rows
    )
    if not duration_rows:
        if target and has_unlinked_product_rule(con, target, "duration_caution"):
            duration["reason"] = "canonical duration product rule is not linked to one criterion"
        else:
            duration["result"] = "not_applicable"
    elif duration_qualifier_review:
        duration["reason"] = "MFDS duration criterion has a qualifier requiring professional review"
    elif prescription_days is None:
        duration["reason"] = "prescription duration is missing or invalid"
    elif duration_parse_reasons or len(distinct_days) != 1:
        duration["reason"] = (
            next(iter(duration_parse_reasons))
            if len(duration_parse_reasons) == 1 and not distinct_days
            else "duration rule is missing, malformed, or ambiguous"
        )
    else:
        maximum = next(iter(distinct_days))
        duration.update({
            "result": "exceeded" if prescription_days > maximum else "within",
            "requested_days": prescription_days, "maximum_days": maximum,
        })
    _dimension_source(duration, duration_rows)

    dose_rows = linked_product_rows(con, target, "dose_caution") if target else []
    dose: dict[str, Any] = {"result": "not_evaluable", "source_scope": "canonical_product"}
    source, source_reason = _source_quantity(dose_rows) if dose_rows else (None, None)
    product_dose_forms = sorted({
        str(row.get("product_dosage_form")).strip()
        for row in dose_rows
        if row.get("product_dosage_form") and str(row.get("product_dosage_form")).strip()
    })
    entered, entered_reason = _draft_quantity(
        draft, product,
        product_dosage_form=", ".join(product_dose_forms) if product_dose_forms else None,
    )
    frequency, frequency_reason = _frequency(draft)
    dose_qualifier_review = any(
        _mfds_criterion_note_requires_review(row) for row in dose_rows
    )
    if not dose_rows:
        if target and has_unlinked_product_rule(con, target, "dose_caution"):
            dose["reason"] = "canonical dose product rule is not linked to one criterion"
        else:
            dose["result"] = "not_applicable"
    elif dose_qualifier_review:
        dose["reason"] = "MFDS dose criterion has a qualifier requiring professional review"
    elif source is None:
        dose["reason"] = source_reason
    elif entered is None:
        dose["reason"] = entered_reason
    elif frequency is None:
        dose["reason"] = frequency_reason
    else:
        threshold_amount, threshold_unit = source
        entered_amount, entered_unit = entered
        daily_amount = None
        if threshold_unit == "mg" and entered_unit == "mg":
            daily_amount = entered_amount * frequency
        elif threshold_unit == "mg" and entered_unit in _COUNT_UNITS:
            dose["reason"] = "count dose requires an authoritative per-unit ingredient content"
        elif threshold_unit in _COUNT_UNITS and entered_unit == threshold_unit:
            daily_amount = entered_amount * frequency
        else:
            dose["reason"] = "dose input and source threshold use incomparable units"
        if daily_amount is not None:
            dose.update({
                "result": "exceeded" if daily_amount > threshold_amount else "within",
                "daily_amount": float(daily_amount),
                "maximum_daily_amount": float(threshold_amount),
                "unit": threshold_unit,
            })
    _dimension_source(dose, dose_rows)
    return {"duration": duration, "dose": dose}


__all__ = ["collect_qualitative_risks", "evaluate_quantitative"]
