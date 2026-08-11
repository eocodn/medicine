"""Pure safety calculations used by the prescription workflows.

This module deliberately does not turn an unparseable DUR value into a safe
answer.  The imported DUR tables contain human-facing text, so every numeric
comparison below has an explicit, conservative parse boundary.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .interaction_timing import courses_overlap, interaction_timing_applies, parse_interaction_timing
APP_TIMEZONE = timezone(timedelta(hours=9), "Asia/Seoul")
AGE_RULE_RE = re.compile(r"(?P<n>\d+)\s*(?P<unit>세|개월|주)\s*(?P<op>미만|이하|이상|초과)")


def _parse_birth_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("birth_date must be YYYY-MM-DD") from exc


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(day=min(value.day, calendar.monthrange(year, month)[1]), year=year, month=month)


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:  # February 29 in a non-leap year has its calendar birthday on February 28.
        return value.replace(month=2, day=28, year=value.year + years)


def age_years(birth_date: str, as_of: date | None = None) -> int:
    """Return completed years, matching the original core calculation."""
    birth = _parse_birth_date(birth_date)
    today = as_of or datetime.now(APP_TIMEZONE).date()
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return max(years, 0)


def age_rule_matches(birth_date: str, rule: str | None, as_of: date | None = None) -> bool:
    """Evaluate Korean age rules such as ``65세 이상`` or ``6개월 미만``."""
    if not rule:
        return False
    match = AGE_RULE_RE.search(rule)
    if not match:
        return False
    birth = _parse_birth_date(birth_date)
    today = as_of or datetime.now(APP_TIMEZONE).date()
    amount = int(match.group("n"))
    unit = match.group("unit")
    operator = match.group("op")
    if unit == "세":
        threshold = _add_years(birth, amount)
        next_threshold = _add_years(birth, amount + 1)
    elif unit == "개월":
        threshold = _add_months(birth, amount)
        next_threshold = _add_months(birth, amount + 1)
    else:
        threshold = birth + timedelta(weeks=amount)
        next_threshold = birth + timedelta(weeks=amount + 1)
    if operator == "미만":
        return today < threshold
    if operator == "이하":
        return today < next_threshold
    if operator == "이상":
        return today >= threshold
    if operator == "초과":
        return today >= next_threshold
    return False


def _row_snapshot(row: Any, columns: Iterable[str] | None = None) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    keys = list(columns or ())
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return {key: value for key, value in zip(keys, row)}


def _query_product_rows(con: sqlite3.Connection, product: Mapping[str, Any], category: str) -> list[dict[str, Any]]:
    code = product.get("product_code") or product.get("edi_code")
    if not code:
        return []
    cursor = con.execute(
        # rowid works with both the imported schema and small test fixtures;
        # source_row is retained in each snapshot when the importer supplied it.
        "SELECT * FROM product_dur WHERE product_code=? AND category=? ORDER BY rowid",
        (code, category),
    )
    columns = [description[0] for description in cursor.description or ()]
    return [_row_snapshot(row, columns) for row in cursor.fetchall()]


def _risk_row_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "notice_no": row.get("notice_no"),
        "notice_date": row.get("notice_date"),
    }


def _combination_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    current: list[dict],
    candidate_course: Mapping[str, Any],
) -> list[dict]:
    risks: list[dict] = []
    code = product.get("product_code") or product.get("edi_code")
    if not code:
        return risks
    for medication in current:
        paired_code = medication.get("product_code")
        if not paired_code:
            continue
        rows = con.execute(
            """
            SELECT ingredient_name, product_code, paired_ingredient_name, paired_product_code,
                   details, notice_no, notice_date
            FROM product_dur
            WHERE category='combination_contraindication'
              AND ((product_code=? AND paired_product_code=?)
                   OR (product_code=? AND paired_product_code=?))
            LIMIT 10
            """,
            (code, paired_code, paired_code, code),
        ).fetchall()
        for row in rows:
            candidate_side = "left" if row["product_code"] == code else "right"
            timing = parse_interaction_timing(
                row["details"], row["ingredient_name"], row["paired_ingredient_name"]
            )
            if not interaction_timing_applies(
                timing, candidate_course, medication, candidate_side=candidate_side
            ):
                continue
            details = row["details"] or "DUR 병용금기 조합에 해당합니다."
            if timing.get("status") == "not_evaluable":
                details = f"{details} 복용 간격 조건은 자동 판정하지 못해 경고를 유지합니다."
            risks.append({
                "type": "combination_contraindication",
                "severity": "danger",
                "title": f"{medication['product_name']}와 병용금기",
                "details": details,
                "related_medication_id": medication["id"],
                "notice_no": row["notice_no"],
                "notice_date": row["notice_date"],
                "timing": timing,
            })
    return risks


def _person_specific_risks(
    con: sqlite3.Connection,
    person: Mapping[str, Any],
    product: Mapping[str, Any],
    as_of: date | None = None,
) -> list[dict]:
    code = product.get("product_code") or product.get("edi_code")
    if not code:
        return []
    rows = con.execute(
        """
        SELECT category, rule_value, details, notice_no, notice_date
        FROM product_dur
        WHERE product_code=?
          AND category IN ('age_contraindication','pregnancy_contraindication','elderly_caution')
        """,
        (code,),
    ).fetchall()
    risks: list[dict] = []
    current_age = age_years(person["birth_date"], as_of)
    for row in rows:
        category = row["category"]
        if category == "age_contraindication":
            if not age_rule_matches(person["birth_date"], row["rule_value"], as_of):
                continue
            title, severity = f"연령금기 · {row['rule_value']}", "danger"
        elif category == "pregnancy_contraindication":
            if person["pregnancy_status"] != "pregnant":
                continue
            title, severity = f"임부금기 · {row['rule_value'] or '등급 미표기'}", "danger"
        else:
            if current_age < 65:
                continue
            title, severity = "노인주의 대상", "warning"
        risks.append({
            "type": category,
            "severity": severity,
            "title": title,
            "details": row["details"],
            "notice_no": row["notice_no"],
            "notice_date": row["notice_date"],
        })
    return risks


def _duplication_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    current: list[dict],
    candidate_course: Mapping[str, Any],
) -> list[dict]:
    code = product.get("product_code") or product.get("edi_code")
    if not code:
        return []
    new_groups = {
        row["rule_value"] for row in con.execute(
            "SELECT DISTINCT rule_value FROM product_dur "
            "WHERE category='therapeutic_duplication_caution' AND product_code=?", (code,)
        ).fetchall() if row["rule_value"]
    }
    risks: list[dict] = []
    for medication in current:
        if courses_overlap(candidate_course, medication) is False:
            continue
        paired_code = medication.get("product_code")
        if not paired_code:
            continue
        groups = {
            row["rule_value"] for row in con.execute(
                "SELECT DISTINCT rule_value FROM product_dur "
                "WHERE category='therapeutic_duplication_caution' AND product_code=?", (paired_code,)
            ).fetchall() if row["rule_value"]
        }
        for group in sorted(new_groups & groups):
            risks.append({
                "type": "therapeutic_duplication_caution",
                "severity": "warning",
                "title": f"효능군 중복주의 · {group}",
                "details": f"현재 복용 중인 {medication['product_name']}와 같은 효능군입니다.",
                "related_medication_id": medication["id"],
            })
    return risks


def _rule_presence_risks(con: sqlite3.Connection, product: Mapping[str, Any]) -> list[dict]:
    code = product.get("product_code") or product.get("edi_code")
    if not code:
        return []
    labels = {"dose_caution": "용량주의 대상", "duration_caution": "투여기간주의 대상"}
    rows = con.execute(
        """
        SELECT category, rule_value, details, notice_no, notice_date
        FROM product_dur WHERE product_code=? AND category IN ('dose_caution','duration_caution')
        """, (code,),
    ).fetchall()
    risks: list[dict] = []
    for row in rows:
        detail = row["details"]
        if row["rule_value"]:
            comparison_hint = (
                "처방 일수를 입력하면 아래에서 DUR 최대 투여기간과 비교합니다."
                if row["category"] == "duration_caution"
                else "1회 복용량과 1일 횟수를 입력하면 아래에서 DUR 1일 최대용량과 비교합니다."
            )
            detail = f"기준: {row['rule_value']}. " + (
                detail or comparison_hint
            )
        risks.append({
            "type": row["category"],
            "severity": "info",
            "title": labels[row["category"]],
            "details": detail,
            "notice_no": row["notice_no"],
            "notice_date": row["notice_date"],
        })
    return risks


def collect_qualitative_risks(
    con: sqlite3.Connection,
    product: Mapping[str, Any],
    person: Mapping[str, Any],
    current: list[dict],
    as_of: date | None = None,
    candidate_course: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Collect the four qualitative DUR risk families used by ``core``."""
    course = candidate_course or {}
    risks = (
        _combination_risks(con, product, current, course)
        + _person_specific_risks(con, person, product, as_of)
        + _duplication_risks(con, product, current, course)
        + _rule_presence_risks(con, product)
    )
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict] = []
    for risk in risks:
        key = (risk.get("type"), risk.get("title"), risk.get("details"), risk.get("related_medication_id"))
        if key not in seen:
            seen.add(key)
            unique.append(risk)
    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(unique, key=lambda risk: (order.get(risk.get("severity"), 9), risk.get("title") or ""))


# Public aliases make the refactor mechanical for callers that used core's names.
combination_risks = _combination_risks
person_specific_risks = _person_specific_risks
duplication_risks = _duplication_risks
rule_presence_risks = _rule_presence_risks


_UNIT_RE = re.compile(r"(?<![\d.])([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*)(mcg|μg|ug|mg|g|정|캡슐|캡|포)?", re.IGNORECASE)
_MASS_TO_MG = {"mcg": Decimal("0.001"), "μg": Decimal("0.001"), "ug": Decimal("0.001"), "mg": Decimal("1"), "g": Decimal("1000")}
_COUNT_UNITS = {"정", "캡슐", "캡", "포"}
_AMBIGUOUS_MARKERS = (
    ",", "，", "/", "~", "또는", "or", "상이", "조건", "환자별", "필요시", "범위",
    "성인", "소아", "신장", "간장애", "체중", "경우", "일때", "일 때", "이상", "이하", "미만", "초과",
)
_MAX_DETAIL_KEYS = ("1일최대투여기준량", "1일최대용량", "일일최대용량", "dailymax", "maximumdaily")
_CONTENT_DETAIL_KEYS = ("점검기준성분함량총함량", "성분함량총함량", "ingredientcontent")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _quantity(value: Any, inherited_unit: str | None = None) -> tuple[Decimal, str | None] | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    text = str(value).strip()
    # Official sources use both 4000 and 4,000.  Remove only grouping commas;
    # commas separating alternative values remain ambiguity markers below.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    if not text or any(marker in text.lower() for marker in _AMBIGUOUS_MARKERS):
        return None
    matches = list(_UNIT_RE.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    # If the number is immediately followed by an unknown word, the optional
    # unit group must not silently discard it (for example, ``10 tablets``).
    trailing = text[match.end():].lstrip()
    if trailing and re.match(r"[A-Za-z가-힣μ]", trailing):
        return None
    # A single numeric token may be surrounded by an ingredient name, but a
    # second number or a conditional/alternative marker is never guessable.
    if len(re.findall(r"(?<![\d.])\d+(?:\.\d+)?", text)) != 1:
        return None
    amount = _decimal(match.group(1))
    if amount is None:
        return None
    unit = (match.group(2) or inherited_unit or "").lower() or None
    if unit in _MASS_TO_MG:
        return amount * _MASS_TO_MG[unit], "mg"
    if unit in _COUNT_UNITS:
        return amount, unit
    return (amount, None) if not unit else None


def _details_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _detail_maximum(details: Any) -> tuple[Decimal, str | None] | None:
    parsed = _details_object(details)
    if parsed is None:
        return None
    candidates: list[Any] = []
    for key, value in parsed.items():
        normalized = re.sub(r"[^0-9a-z가-힣]", "", str(key).lower())
        if normalized in _MAX_DETAIL_KEYS or ("최대" in normalized and ("투여" in normalized or "용량" in normalized)):
            candidates.append(value)
    if len(candidates) != 1:
        return None
    return _quantity(candidates[0])


def _detail_content(details: Any) -> Decimal | None:
    parsed = _details_object(details)
    if parsed is None:
        return None
    candidates = []
    for key, value in parsed.items():
        normalized = re.sub(r"[^0-9a-z가-힣]", "", str(key).lower())
        if normalized in _CONTENT_DETAIL_KEYS or ("성분함량" in normalized and "총함량" in normalized):
            candidates.append(value)
    if len(candidates) != 1:
        return None
    quantity = _quantity(candidates[0], inherited_unit="mg")
    return quantity[0] if quantity and quantity[1] == "mg" else None


def _source_quantity(rows: list[dict[str, Any]]) -> tuple[tuple[Decimal, str | None] | None, str | None]:
    if not rows:
        return None, "dose rule is missing"
    if len(rows) != 1:
        return None, "dose rule has multiple rows"
    row = rows[0]
    original = _quantity(row.get("rule_value"))
    structured = _detail_maximum(row.get("details"))
    if original is None or structured is None:
        return None, "dose rule value or structured details are not a single numeric threshold"
    original_amount, original_unit = original
    structured_amount, structured_unit = structured
    unit = original_unit or structured_unit
    if original_unit and structured_unit and original_unit != structured_unit:
        return None, "dose rule has no unambiguous unit"
    if original_unit is None:
        original_amount = (original_amount * _MASS_TO_MG[unit]) if unit in _MASS_TO_MG else original_amount
    if structured_unit is None:
        structured_amount = (structured_amount * _MASS_TO_MG[unit]) if unit in _MASS_TO_MG else structured_amount
    if original_amount != structured_amount:
        return None, "dose rule and structured details conflict"
    return (original_amount, unit), None


def _countable_form(unit: str, dosage_form: Any) -> bool:
    form = str(dosage_form or "").lower()
    if unit == "정":
        return "정" in form
    if unit in {"캡슐", "캡"}:
        return "캡" in form
    if unit == "포":
        return "포" in form or "산제" in form or "과립" in form
    return False


def _draft_quantity(draft: Mapping[str, Any], product: Mapping[str, Any]) -> tuple[tuple[Decimal, str] | None, str | None]:
    amount = draft.get("dose_amount")
    unit = str(draft.get("dose_unit") or "").strip().lower()
    if amount is None and draft.get("dosage_text"):
        parsed = _quantity(draft["dosage_text"])
        if parsed:
            amount, unit = parsed[0], parsed[1] or ""
    amount_decimal = _decimal(amount)
    if amount_decimal is None or unit not in _MASS_TO_MG and unit not in _COUNT_UNITS:
        return None, "dose input is missing or has an unsupported unit"
    if unit in _COUNT_UNITS:
        if not _countable_form(unit, product.get("dosage_form")):
            return None, "count dose requires a corresponding countable dosage form"
        return (amount_decimal, unit), None
    return (amount_decimal * _MASS_TO_MG[unit], "mg"), None


def _frequency(draft: Mapping[str, Any]) -> tuple[Decimal | None, str | None]:
    value = draft.get("frequency_per_day")
    if value is None and not bool(draft.get("as_needed", draft.get("prn", False))):
        schedules = draft.get("schedule_times")
        if isinstance(schedules, (list, tuple)) and schedules:
            value = len(schedules)
    frequency = _decimal(value)
    if frequency is None:
        return None, "daily frequency is missing (PRN without frequency is not evaluable)"
    if frequency != frequency.to_integral_value():
        return None, "daily frequency must be a positive integer"
    return frequency, None


def _dimension_source(result: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    result["source_rows"] = rows
    if len(rows) == 1:
        result["source"] = rows[0]
    return result


def evaluate_quantitative(con: sqlite3.Connection, product: dict, draft: dict) -> dict:
    """Evaluate duration and daily dose without classifying ambiguous data."""
    duration_rows = _query_product_rows(con, product, "duration_caution")
    duration: dict[str, Any] = {"result": "not_evaluable"}
    raw_days = draft.get("prescription_days")
    prescription_days: int | None = None
    if raw_days is not None and not isinstance(raw_days, bool):
        try:
            parsed_days = Decimal(str(raw_days).strip())
            if parsed_days.is_finite() and parsed_days > 0 and parsed_days == parsed_days.to_integral_value():
                prescription_days = int(parsed_days)
        except (InvalidOperation, AttributeError, ValueError):
            prescription_days = None
    distinct_days: set[int] = set()
    malformed_duration = False
    for row in duration_rows:
        text = str(row.get("rule_value") or "").strip()
        matches = re.findall(r"(?<!\d)\d+(?!\d)", text)
        if len(matches) != 1 or int(matches[0]) <= 0 or any(marker in text for marker in _AMBIGUOUS_MARKERS):
            malformed_duration = True
            continue
        distinct_days.add(int(matches[0]))
    if not duration_rows:
        duration["result"] = "not_applicable"
    elif prescription_days is None or prescription_days <= 0:
        duration["reason"] = "prescription duration is missing or invalid"
    elif malformed_duration or len(distinct_days) != 1:
        duration["reason"] = "duration rule is missing, malformed, or ambiguous"
    else:
        maximum_days = next(iter(distinct_days))
        duration.update({
            "result": "exceeded" if prescription_days > maximum_days else "within",
            "requested_days": prescription_days,
            "maximum_days": maximum_days,
        })
    _dimension_source(duration, duration_rows)

    dose: dict[str, Any] = {"result": "not_evaluable"}
    dose_rows = _query_product_rows(con, product, "dose_caution")
    source, source_reason = _source_quantity(dose_rows) if dose_rows else (None, None)
    entered, entered_reason = _draft_quantity(draft, product)
    frequency, frequency_reason = _frequency(draft)
    if not dose_rows:
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
        # A unitless numeric source is only safe as a count when both the
        # entered unit and the dosage form establish what is being counted.
        if threshold_unit is None and entered_unit in _COUNT_UNITS and _countable_form(entered_unit, product.get("dosage_form")):
            threshold_unit = entered_unit
        if threshold_unit == "mg" and entered_unit == "mg":
            daily_amount = entered_amount * frequency
        elif threshold_unit == "mg" and entered_unit in _COUNT_UNITS:
            content = _detail_content(dose_rows[0].get("details")) if len(dose_rows) == 1 else None
            if content is None:
                dose["reason"] = "count dose requires an unambiguous per-unit ingredient content"
                daily_amount = None
            else:
                daily_amount = entered_amount * frequency * content
                dose["per_unit_ingredient_amount"] = float(content)
        elif threshold_unit in _COUNT_UNITS and entered_unit == threshold_unit:
            daily_amount = entered_amount * frequency
        else:
            dose["reason"] = "dose input and source threshold use incomparable units"
            daily_amount = None
        if daily_amount is not None:
            dose.update({
                "result": "exceeded" if daily_amount > threshold_amount else "within",
                "daily_amount": float(daily_amount),
                "maximum_daily_amount": float(threshold_amount),
                "unit": threshold_unit,
            })
    _dimension_source(dose, dose_rows)
    return {"duration": duration, "dose": dose}


__all__ = [
    "age_years", "age_rule_matches", "collect_qualitative_risks",
    "combination_risks", "person_specific_risks", "duplication_risks", "rule_presence_risks",
    "evaluate_quantitative",
]
