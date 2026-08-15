from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

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


def _pregnancy_rule_display(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"1", "2"}:
        return f"{text}등급"
    return text or "등급 미표기"


def _pregnancy_rule_is_conditional(value: Any) -> bool:
    """Return whether the DUR grade itself carries an applicability qualifier.

    Canonical pregnancy criteria use plain ``1등급``/``2등급`` for unconditional
    grades and parenthesized text for form, indication, duration, gestational-age,
    or explicit exception conditions. Those conditions are authoritative evidence,
    but the current medication/profile inputs do not model every required clinical
    fact, so they must stay review-required without being presented as a definite hit.
    """
    text = str(value or "").strip()
    return bool(re.search(r"[12]\s*등급\s*\(", text))


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
            timing = parse_interaction_timing(
                details, _canonical_ingredient(row), _canonical_paired_ingredient(row)
            )
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
            if timing.get("status") == "not_evaluable":
                finding["evaluation_status"] = "conditional"
            risks.append(finding)
        if not rows and _unlinked_combination_exists(con, target, paired):
            risks.append({
                "type": "combination_contraindication", "severity": "info",
                "title": f"{medication['product_name']}와 병용금기 기준 확인 필요",
                "details": "MFDS ITEM_SEQ 병용금기 규칙은 있으나 XLSX 상세 기준 연결을 확정하지 못했습니다.",
                "related_medication_id": medication["id"],
                "evaluation_status": "unknown", "source_scope": "canonical_product",
            })
    return risks


def _person_specific_risks(
    con: sqlite3.Connection,
    person: Mapping[str, Any],
    product: Mapping[str, Any],
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    target = item_seq(product)
    if not target:
        return []
    rows: list[dict[str, Any]] = []
    for category in ("age_contraindication", "pregnancy_contraindication", "elderly_caution"):
        rows.extend(linked_product_rows(con, target, category))
    risks: list[dict[str, Any]] = []
    current_age = age_years(person["birth_date"], as_of)
    for row in rows:
        category = str(row["category"])
        rule_value = row.get("criterion_rule_value")
        if category == "age_contraindication":
            applies, reason = age_rule_evaluation(
                person["birth_date"], rule_value,
                row.get("product_dosage_form") or product.get("dosage_form"),
                as_of,
            )
            if applies is None:
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
            if not applies:
                continue
            title, severity = f"연령금기 · {rule_value}", "danger"
        elif category == "pregnancy_contraindication":
            if person.get("pregnancy_status") != "pregnant":
                continue
            rule_display = _pregnancy_rule_display(rule_value)
            title, severity = f"임부금기 · {rule_display}", "danger"
        else:
            if current_age < 65:
                continue
            title, severity = "노인주의 대상", "warning"
        finding = {
            "type": category, "severity": severity, "title": title,
            "details": _canonical_details(row), "source_scope": "canonical_product",
            "dataset_key": row.get("criterion_source_dataset_key"),
            "source_row": row.get("criterion_source_row"),
        }
        if category == "pregnancy_contraindication" and _pregnancy_rule_is_conditional(rule_value):
            finding["evaluation_status"] = "conditional"
        risks.append(finding)
    return risks


def _duplication_groups(con: sqlite3.Connection, product: Mapping[str, Any]) -> set[str]:
    target = item_seq(product)
    if not target:
        return set()
    groups = set()
    for row in linked_product_rows(con, target, "therapeutic_duplication_caution"):
        value = row.get("criterion_rule_value") or row.get("effect_name")
        if value:
            groups.add(str(value))
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
        for group in sorted(new_groups & _duplication_groups(con, medication)):
            risks.append({
                "type": "therapeutic_duplication_caution", "severity": "warning",
                "title": f"효능군 중복주의 · {group}",
                "details": f"현재 복용 중인 {medication['product_name']}와 같은 효능군입니다.",
                "related_medication_id": medication["id"], "source_scope": "canonical_product",
            })
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
        + _person_specific_risks(con, person, product, as_of)
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
    if not duration_rows:
        if target and has_unlinked_product_rule(con, target, "duration_caution"):
            duration["reason"] = "canonical duration product rule is not linked to one criterion"
        else:
            duration["result"] = "not_applicable"
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
    if not dose_rows:
        if target and has_unlinked_product_rule(con, target, "dose_caution"):
            dose["reason"] = "canonical dose product rule is not linked to one criterion"
        else:
            dose["result"] = "not_applicable"
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
