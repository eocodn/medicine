from __future__ import annotations

import re
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Any


_NUMBER_RE = re.compile(r"(?<![\d.])([+]?(?:\d+(?:\.\d*)?|\.\d+))")
_QUANTITY_RE = re.compile(
    r"(?<![\d.])([+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(마이크로그램|밀리그램|그램|mcg|μg|ug|㎍|mg|g|정|캡슐|캡|포)",
    re.IGNORECASE,
)
_MASS_TO_MG = {
    "마이크로그램": Decimal("0.001"),
    "밀리그램": Decimal("1"),
    "그램": Decimal("1000"),
    "mcg": Decimal("0.001"),
    "μg": Decimal("0.001"),
    "ug": Decimal("0.001"),
    "㎍": Decimal("0.001"),
    "mg": Decimal("1"),
    "g": Decimal("1000"),
}
_COUNT_UNITS = {"정": "정", "캡슐": "캡슐", "캡": "캡슐", "포": "포"}
_AMBIGUOUS_MARKERS = (
    "/", "~", "또는", " or ", "상이", "조건", "환자별", "필요시", "필요 시", "범위",
    "성인", "소아", "신장", "간장애", "간 장애", "체중", "경우", "일때", "일 때",
    "이상", "이하", "미만", "초과",
)
_NOT_SINGLE_REASON = "dose criterion is not one unconditional numeric threshold"


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def parse_daily_dose_threshold(
    value: Any,
) -> tuple[str | None, str | None, str, str | None]:
    """Normalize one unconditional official daily-dose threshold.

    Source text may include an ingredient name and descriptive parentheticals, but
    conditional, alternative, multi-quantity, or unitless expressions remain
    explicitly non-evaluable instead of being guessed.
    """
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None, None, "not_evaluable", "dose criterion is missing"
    text = str(value).strip()
    if not text:
        return None, None, "not_evaluable", "dose criterion is missing"
    # Official files use grouping commas in quantities. Remove only 1,000-style
    # separators; other commas remain harmless prose separators.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    lowered = text.lower()
    if any(marker in lowered for marker in _AMBIGUOUS_MARKERS):
        return None, None, "not_evaluable", _NOT_SINGLE_REASON
    numbers = list(_NUMBER_RE.finditer(text))
    quantities = list(_QUANTITY_RE.finditer(text))
    if len(numbers) != 1 or len(quantities) != 1:
        return None, None, "not_evaluable", _NOT_SINGLE_REASON
    amount = _decimal(quantities[0].group(1))
    if amount is None:
        return None, None, "not_evaluable", _NOT_SINGLE_REASON
    unit = quantities[0].group(2).lower()
    if unit in _MASS_TO_MG:
        return _decimal_text(amount * _MASS_TO_MG[unit]), "mg", "parsed", None
    if unit in _COUNT_UNITS:
        return _decimal_text(amount), _COUNT_UNITS[unit], "parsed", None
    return None, None, "not_evaluable", _NOT_SINGLE_REASON


def materialize_dose_criteria(con: sqlite3.Connection) -> dict[str, int]:
    """Materialize structured dose criteria from authoritative XLSX rule text."""
    rows = con.execute(
        """SELECT id,rule_value FROM ingredient_rules
           WHERE category='dose_caution' ORDER BY id"""
    ).fetchall()
    con.execute("DELETE FROM dose_criteria")
    parsed = 0
    not_evaluable = 0
    for criterion_rule_id, rule_value in rows:
        amount, unit, status, reason = parse_daily_dose_threshold(rule_value)
        con.execute(
            """INSERT INTO dose_criteria(
                   criterion_rule_id,maximum_daily_amount,maximum_daily_unit,parse_status,parse_reason
               ) VALUES(?,?,?,?,?)""",
            (criterion_rule_id, amount, unit, status, reason),
        )
        if status == "parsed":
            parsed += 1
        else:
            not_evaluable += 1
    return {
        "dose_criteria_materialized": len(rows),
        "dose_criteria_parsed": parsed,
        "dose_criteria_not_evaluable": not_evaluable,
    }


__all__ = ["materialize_dose_criteria", "parse_daily_dose_threshold"]
