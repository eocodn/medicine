"""Pure age and quantitative parsing primitives for canonical safety evaluation.

Unparseable official values remain explicitly non-evaluable; this module contains no
reference-database lookup or product identity logic.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .dosage_forms import dosage_form_tags

APP_TIMEZONE = timezone(timedelta(hours=9), "Asia/Seoul")
AGE_RULE_RE = re.compile(r"(?P<n>\d+)\s*(?P<unit>세|개월|주|일)\s*(?P<op>미만|이하|이상|초과)")


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


def _age_match_applies(birth_date: str, match: re.Match[str], as_of: date | None = None) -> bool:
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
    elif unit == "주":
        threshold = birth + timedelta(weeks=amount)
        next_threshold = birth + timedelta(weeks=amount + 1)
    else:
        threshold = birth + timedelta(days=amount)
        next_threshold = birth + timedelta(days=amount + 1)
    if operator == "미만":
        return today < threshold
    if operator == "이하":
        return today < next_threshold
    if operator == "이상":
        return today >= threshold
    if operator == "초과":
        return today >= next_threshold
    return False


def age_rule_matches(birth_date: str, rule: str | None, as_of: date | None = None) -> bool:
    """Evaluate one unambiguous Korean age expression."""
    if not rule:
        return False
    matches = list(AGE_RULE_RE.finditer(rule))
    return len(matches) == 1 and _age_match_applies(birth_date, matches[0], as_of)


def age_rule_evaluation(
    birth_date: str,
    rule: str | None,
    product_dosage_form: object,
    as_of: date | None = None,
) -> tuple[bool | None, str | None]:
    """Resolve a product-specific age rule, or return unknown instead of guessing.

    Multi-form MFDS criteria encode each form group immediately before its age
    expression (for example ``액제: 2세 미만, 정제: 6세 미만``). Every age
    expression must have an explicit form group, and the product form must map
    to exactly one group. Any other structure stays non-evaluable.
    """
    if not rule:
        return None, "연령금기 기준값이 없습니다."
    matches = list(AGE_RULE_RE.finditer(rule))
    if not matches:
        return None, "연령금기 기준을 자동으로 해석하지 못했습니다."
    if len(matches) == 1:
        return _age_match_applies(birth_date, matches[0], as_of), None

    product_tags = dosage_form_tags(product_dosage_form)
    if not product_tags:
        return None, "제품 제형을 확정하지 못해 제형별 연령금기 기준을 자동 판정하지 못했습니다."

    groups: list[tuple[set[str], re.Match[str]]] = []
    previous_end = 0
    for match in matches:
        prefix = rule[previous_end:match.start()]
        previous_end = match.end()
        if ":" not in prefix:
            return None, "제형별 연령금기 조건 구조를 하나로 확정하지 못했습니다."
        form_text = prefix.rsplit(":", 1)[0]
        tags = dosage_form_tags(form_text)
        if not tags:
            return None, "제형별 연령금기 조건의 제형을 자동으로 해석하지 못했습니다."
        groups.append((tags, match))

    applicable = [match for tags, match in groups if tags & product_tags]
    if len(applicable) != 1:
        return None, "제품 제형에 적용되는 연령금기 조건을 하나로 확정하지 못했습니다."
    return _age_match_applies(birth_date, applicable[0], as_of), None


_UNIT_RE = re.compile(r"(?<![\d.])([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*)(mcg|μg|ug|mg|g|정|캡슐|캡|포)?", re.IGNORECASE)
_MASS_TO_MG = {"mcg": Decimal("0.001"), "μg": Decimal("0.001"), "ug": Decimal("0.001"), "mg": Decimal("1"), "g": Decimal("1000")}
_COUNT_UNITS = {"정", "캡슐", "캡", "포"}
_AMBIGUOUS_MARKERS = (
    ",", "，", "/", "~", "또는", "or", "상이", "조건", "환자별", "필요시", "범위",
    "성인", "소아", "신장", "간장애", "체중", "경우", "일때", "일 때", "이상", "이하", "미만", "초과",
)


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


def _source_quantity(rows: list[dict[str, Any]]) -> tuple[tuple[Decimal, str] | None, str | None]:
    if not rows:
        return None, "dose rule is missing"
    quantities: set[tuple[Decimal, str]] = set()
    reasons: set[str] = set()
    for row in rows:
        if row.get("dose_parse_status") != "parsed":
            reasons.add(str(row.get("dose_parse_reason") or "canonical dose criterion is not quantitatively evaluable"))
            continue
        amount = _decimal(row.get("maximum_daily_amount"))
        unit = str(row.get("maximum_daily_unit") or "").strip().lower()
        if amount is None or unit not in _MASS_TO_MG and unit not in _COUNT_UNITS:
            reasons.add("canonical dose criterion has an invalid structured threshold")
            continue
        quantities.add((amount, "캡슐" if unit == "캡" else unit))
    if reasons:
        if not quantities and len(reasons) == 1:
            return None, next(iter(reasons))
        return None, "dose criteria do not resolve to one unambiguous daily threshold"
    if len(quantities) != 1:
        return None, "dose criteria do not resolve to one unambiguous daily threshold"
    return next(iter(quantities)), None


def _countable_form(unit: str, dosage_form: Any) -> bool:
    form = str(dosage_form or "").lower()
    if unit == "정":
        return "정" in form
    if unit in {"캡슐", "캡"}:
        return "캡" in form
    if unit == "포":
        return "포" in form or "산제" in form or "과립" in form
    return False


def _draft_quantity(
    draft: Mapping[str, Any],
    product: Mapping[str, Any],
    *,
    product_dosage_form: Any = None,
) -> tuple[tuple[Decimal, str] | None, str | None]:
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
        dosage_form = product_dosage_form if product_dosage_form is not None else product.get("dosage_form")
        if not _countable_form(unit, dosage_form):
            return None, "count dose requires a corresponding countable dosage form"
        return (amount_decimal, "캡슐" if unit == "캡" else unit), None
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
