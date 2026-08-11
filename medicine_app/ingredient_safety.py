from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .coverage import normalize_ingredient_name
from .interaction_timing import courses_overlap, interaction_timing_applies, parse_interaction_timing
from .safety import age_rule_matches, age_years


_CONDITIONAL_NOTE_MARKERS = ("초과", "이하", "미만", "이상", "경우", "한함", "제외", "이내", "투여시")


def _is_conditional_note(value: Any) -> bool:
    note = str(value or "").strip()
    return bool(note) and (bool(re.search(r"\d", note)) or any(marker in note for marker in _CONDITIONAL_NOTE_MARKERS))


def _not_evaluable(
    row: Mapping[str, Any],
    category: str,
    reason: str,
    *,
    related_medication_id: str | None = None,
) -> dict[str, Any]:
    item = {
        "category": category,
        "result": "not_evaluable",
        "reason": reason,
        "source_scope": "ingredient",
        "dataset_key": row.get("dataset_key"),
        "source_row": row.get("source_row"),
    }
    if related_medication_id:
        item["related_medication_id"] = related_medication_id
    return item


def _rows(con: sqlite3.Connection, category: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(
            "SELECT * FROM ingredient_dur WHERE category=? ORDER BY rowid", (category,)
        ).fetchall()]
    except sqlite3.OperationalError:
        return []


def _ingredients(product: Mapping[str, Any]) -> set[str]:
    return {normalize_ingredient_name(value) for value in product.get("safety_ingredients") or [] if value}


def _form_applicable(rule_form: Any, product_form: Any) -> bool | None:
    if not rule_form:
        return True
    if not product_form:
        return None
    rule = re.sub(r"\s+", "", str(rule_form).casefold())
    product = re.sub(r"\s+", "", str(product_form).casefold())
    tokens = [token for token in re.split(r"[,/]", rule) if token]
    for token in tokens:
        if token == product or (len(token) >= 2 and (token in product or product in token)):
            return True
    return False


def evaluate_ingredient_rule_applicability(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    category: str,
) -> dict[str, Any]:
    """Resolve whether a current ingredient rule applies without guessing.

    An exact ingredient mapping makes an empty match a candidate for
    ``not_applicable``; the assessment layer separately requires a verified
    dataset before allowing the UI to hide it. A matching rule with unresolved
    form or condition is still a real rule and remains observable.
    """
    result: dict[str, Any] = {"result": "not_evaluable", "source_scope": "ingredient"}
    ingredients = _ingredients(product)
    if not ingredients:
        result["reason"] = "ingredient mapping is unavailable"
        return result
    matching = [
        row for row in _rows(con, category)
        if normalize_ingredient_name(row.get("ingredient_name")) in ingredients
    ]
    if not matching:
        result["result"] = "not_applicable"
        result.pop("reason", None)
        return result
    applicable: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in matching:
        form = _form_applicable(row.get("dosage_form"), product.get("dosage_form"))
        if form is True:
            applicable.append(row)
        elif form is None:
            unresolved.append(row)
    if applicable:
        conditional = [str(row.get("note") or "").strip() for row in applicable if _is_conditional_note(row.get("note"))]
        if conditional:
            result.update({
                "reason": f"ingredient {category} rule condition cannot be resolved: {' / '.join(conditional)}",
                "source_rows": applicable,
            })
            return result
        result.update({"result": "applicable", "source_rows": applicable})
        if len(applicable) == 1:
            result["source"] = applicable[0]
        return result
    if unresolved:
        result.update({
            "reason": f"ingredient {category} rule dosage form cannot be resolved",
            "source_rows": unresolved,
        })
        return result
    result["result"] = "not_applicable"
    result.pop("reason", None)
    return result


def _risk(row: Mapping[str, Any], *, type_: str, severity: str, title: str, details: str | None = None) -> dict[str, Any]:
    return {
        "type": type_,
        "severity": severity,
        "title": title,
        "details": details or row.get("details") or row.get("note"),
        "source_scope": "ingredient",
        "dataset_key": row.get("dataset_key"),
        "source_row": row.get("source_row"),
    }


def collect_ingredient_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
    current: list[dict],
    as_of: date | None = None,
    candidate_course: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    new_ingredients = _ingredients(product)
    if not new_ingredients:
        return [], []
    risks: list[dict[str, Any]] = []
    not_evaluable: list[dict[str, Any]] = []
    course = candidate_course or {}

    for medication in current:
        current_ingredients = _ingredients(medication)
        if not current_ingredients:
            continue
        for row in _rows(con, "combination_contraindication"):
            left = normalize_ingredient_name(row.get("ingredient_name"))
            right = normalize_ingredient_name(row.get("paired_ingredient_name"))
            if not left or not right:
                continue
            left_match = left in new_ingredients and right in current_ingredients
            right_match = right in new_ingredients and left in current_ingredients
            if left_match or right_match:
                timing_text = " ".join(
                    part
                    for part in (
                        str(row.get("details") or "").strip(),
                        str(row.get("note") or "").strip(),
                    )
                    if part
                )
                timing = parse_interaction_timing(
                    timing_text, row.get("ingredient_name"), row.get("paired_ingredient_name")
                )
                if not interaction_timing_applies(
                    timing, course, medication, candidate_side="left" if left_match else "right"
                ):
                    continue
                note_text = str(row.get("note") or "").strip()
                note_timing = parse_interaction_timing(
                    note_text, row.get("ingredient_name"), row.get("paired_ingredient_name")
                )
                note_is_only_structured_timing = (
                    note_timing.get("status") == "structured"
                    and note_timing.get("kind") in {"minimum_separation", "washout_after"}
                )
                conditional_note = (
                    note_text
                    if _is_conditional_note(note_text) and not note_is_only_structured_timing
                    else ""
                )
                details = row.get("details") or "성분 기준 DUR 병용금기 조합에 해당합니다."
                if conditional_note:
                    details = f"조건: {conditional_note}. {details}"
                if timing.get("status") == "not_evaluable":
                    details = f"{details} 복용 간격 조건은 자동 판정하지 못해 경고를 유지합니다."
                item = _risk(
                    row,
                    type_="combination_contraindication",
                    severity="danger",
                    title=(
                        f"{medication['product_name']}와 성분 병용금기 조건 확인 필요"
                        if conditional_note else f"{medication['product_name']}와 성분 병용금기"
                    ),
                    details=details,
                )
                item["related_medication_id"] = medication["id"]
                item["timing"] = timing
                if conditional_note or timing.get("status") == "not_evaluable":
                    item["evaluation_status"] = "unknown"
                risks.append(item)

    current_age = age_years(person["birth_date"], as_of)
    for row in _rows(con, "age_contraindication"):
        if normalize_ingredient_name(row.get("ingredient_name")) not in new_ingredients:
            continue
        if not age_rule_matches(person["birth_date"], row.get("rule_value"), as_of):
            continue
        applicability = _form_applicable(row.get("dosage_form"), product.get("dosage_form"))
        if applicability is None:
            not_evaluable.append(_not_evaluable(
                row,
                "age_contraindication",
                "제품 제형 정보가 없어 성분 연령금기의 제형 적용 여부를 판정할 수 없습니다.",
            ))
            continue
        if applicability is False:
            continue
        risks.append(_risk(
            row, type_="age_contraindication", severity="danger",
            title=f"성분 연령금기 · {row.get('rule_value') or '기준 확인 필요'}",
        ))

    if person.get("pregnancy_status") == "pregnant":
        for row in _rows(con, "pregnancy_contraindication"):
            if normalize_ingredient_name(row.get("ingredient_name")) in new_ingredients:
                risks.append(_risk(
                    row, type_="pregnancy_contraindication", severity="danger",
                    title=f"성분 임부금기 · {row.get('rule_value') or '등급 미표기'}",
                ))

    if person.get("lactation_status") == "breastfeeding":
        for row in _rows(con, "lactation_caution"):
            if normalize_ingredient_name(row.get("ingredient_name")) not in new_ingredients:
                continue
            applicability = _form_applicable(row.get("dosage_form"), product.get("dosage_form"))
            if applicability is None:
                not_evaluable.append(_not_evaluable(
                    row,
                    "lactation_caution",
                    "제품 제형 정보가 없어 성분 수유부주의의 제형 적용 여부를 판정할 수 없습니다.",
                ))
                continue
            if applicability is True:
                risks.append(_risk(
                    row, type_="lactation_caution", severity="warning", title="성분 수유부주의 대상",
                ))

    if current_age >= 65:
        for row in _rows(con, "elderly_caution"):
            if normalize_ingredient_name(row.get("ingredient_name")) not in new_ingredients:
                continue
            applicability = _form_applicable(row.get("dosage_form"), product.get("dosage_form"))
            if applicability is None:
                not_evaluable.append(_not_evaluable(
                    row,
                    "elderly_caution",
                    "제품 제형 정보가 없어 성분 노인주의의 제형 적용 여부를 판정할 수 없습니다.",
                ))
                continue
            if applicability is True:
                risks.append(_risk(
                    row, type_="elderly_caution", severity="warning", title="성분 노인주의 대상",
                ))

    duplication_rows = _rows(con, "therapeutic_duplication_caution")
    new_groups: dict[str, list[dict[str, Any]]] = {}
    for row in duplication_rows:
        if normalize_ingredient_name(row.get("ingredient_name")) in new_ingredients and row.get("rule_value"):
            new_groups.setdefault(str(row["rule_value"]), []).append(row)
    for medication in current:
        if courses_overlap(course, medication) is False:
            continue
        current_ingredients = _ingredients(medication)
        if not current_ingredients:
            continue
        current_groups: dict[str, list[dict[str, Any]]] = {}
        for row in duplication_rows:
            if normalize_ingredient_name(row.get("ingredient_name")) in current_ingredients and row.get("rule_value"):
                current_groups.setdefault(str(row["rule_value"]), []).append(row)
        for group in sorted(set(new_groups) & set(current_groups)):
            source_rows = new_groups[group] + current_groups[group]
            conditional_notes = sorted({
                str(row.get("note") or "").strip()
                for row in source_rows
                if _is_conditional_note(row.get("note"))
            })
            source_row = new_groups[group][0]
            if conditional_notes:
                reason = " / ".join(conditional_notes)
                item = _risk(
                    source_row, type_="therapeutic_duplication_caution", severity="info",
                    title=f"성분 효능군 중복 기준 판정 불가 · {group}",
                    details=f"현재 복용 중인 {medication['product_name']}와 같은 효능군이지만 조건을 자동 판정하지 못했습니다: {reason}",
                )
                item["related_medication_id"] = medication["id"]
                risks.append(item)
                continue
            item = _risk(
                source_row, type_="therapeutic_duplication_caution", severity="warning",
                title=f"성분 효능군 중복주의 · {group}",
                details=f"현재 복용 중인 {medication['product_name']}와 같은 성분 효능군입니다.",
            )
            item["related_medication_id"] = medication["id"]
            risks.append(item)

    labels = {"dose_caution": "용량주의", "duration_caution": "투여기간주의"}
    for category, label in labels.items():
        for row in _rows(con, category):
            if normalize_ingredient_name(row.get("ingredient_name")) not in new_ingredients:
                continue
            applicability = _form_applicable(row.get("dosage_form"), product.get("dosage_form"))
            if applicability is None:
                not_evaluable.append(_not_evaluable(
                    row,
                    category,
                    f"제품 제형 정보가 없어 성분 {label} 규칙의 적용 여부를 판정할 수 없습니다.",
                ))

    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for item in risks:
        key = (item.get("type"), item.get("title"), item.get("related_medication_id"), item.get("dataset_key"), item.get("source_row"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    issue_seen: set[tuple[Any, ...]] = set()
    unique_issues: list[dict[str, Any]] = []
    for item in not_evaluable:
        key = (item.get("category"), item.get("reason"), item.get("related_medication_id"), item.get("dataset_key"), item.get("source_row"))
        if key not in issue_seen:
            issue_seen.add(key)
            unique_issues.append(item)
    order = {"danger": 0, "warning": 1, "info": 2}
    return (
        sorted(unique, key=lambda item: (order.get(item.get("severity"), 9), item.get("title") or "")),
        unique_issues,
    )


def evaluate_ingredient_duration(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> dict[str, Any]:
    applicability = evaluate_ingredient_rule_applicability(con, product, "duration_caution")
    if applicability["result"] != "applicable":
        return applicability
    result: dict[str, Any] = {"result": "not_evaluable", "source_scope": "ingredient"}
    raw_days = draft.get("prescription_days")
    try:
        parsed_days = Decimal(str(raw_days)) if raw_days is not None and not isinstance(raw_days, bool) else None
    except (InvalidOperation, ValueError):
        parsed_days = None
    if parsed_days is None or not parsed_days.is_finite() or parsed_days <= 0 or parsed_days != parsed_days.to_integral_value():
        result["reason"] = "prescription duration is missing or invalid"
        return result

    applicable = applicability["source_rows"]
    thresholds: set[int] = set()
    for row in applicable:
        text = str(row.get("rule_value") or "")
        matches = re.findall(r"(?<!\d)(\d+)\s*일(?!\d)", text)
        if len(matches) != 1:
            result["reason"] = "ingredient duration rule is malformed or ambiguous"
            result["source_rows"] = applicable
            return result
        thresholds.add(int(matches[0]))
    if len(thresholds) != 1:
        result["reason"] = "ingredient duration rules have multiple thresholds"
        result["source_rows"] = applicable
        return result
    maximum = next(iter(thresholds))
    requested = int(parsed_days)
    result.update({
        "result": "exceeded" if requested > maximum else "within",
        "requested_days": requested,
        "maximum_days": maximum,
        "source_rows": applicable,
    })
    if len(applicable) == 1:
        result["source"] = applicable[0]
    return result


__all__ = [
    "collect_ingredient_risks", "evaluate_ingredient_duration",
    "evaluate_ingredient_rule_applicability",
]
