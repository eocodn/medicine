from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Mapping


_WITHIN_HOURS_RE = re.compile(r"(?P<hours>\d+)\s*시간\s*이내\s*병용금기", re.IGNORECASE)
_AFTER_COURSE_RE = re.compile(
    r"(?P<subject>[^,，.;。]{1,120}?)\s*투여\s*중\s*및\s*종료\s*후\s*"
    r"(?P<amount>\d+)\s*(?P<unit>시간|일|주)\s*간",
    re.IGNORECASE,
)
_POST_COURSE_MARKERS = ("종료 후", "중단 후", "중단한 직후", "중단 직후", "투여 중 및 종료 후")


def _tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return set(re.findall(r"[0-9a-z가-힣]+", text))


def _source_side(subject: str, left_ingredient: Any, right_ingredient: Any) -> str | None:
    subject_tokens = _tokens(subject)
    if not subject_tokens:
        return None
    left_tokens = _tokens(left_ingredient)
    right_tokens = _tokens(right_ingredient)
    left_match = bool(left_tokens) and (
        subject_tokens.issubset(left_tokens) or left_tokens.issubset(subject_tokens)
    )
    right_match = bool(right_tokens) and (
        subject_tokens.issubset(right_tokens) or right_tokens.issubset(subject_tokens)
    )
    if left_match == right_match:
        return None
    return "left" if left_match else "right"


def parse_interaction_timing(
    text: Any,
    left_ingredient: Any,
    right_ingredient: Any,
) -> dict[str, Any]:
    """Structure only timing language whose duration and direction are explicit.

    DUR interaction reasons contain both repeated machine-resolvable wording and
    free text. Unresolved post-stop wording is intentionally surfaced as
    ``not_evaluable`` rather than guessed or silently reduced to overlap-only.
    """
    source_text = " ".join(str(text or "").split())
    match = _WITHIN_HOURS_RE.search(source_text)
    if match:
        amount = int(match.group("hours"))
        return {
            "status": "structured",
            "kind": "minimum_separation",
            "hours": amount,
            "amount": amount,
            "unit": "시간",
            "direction": "symmetric",
            "source_text": source_text,
        }
    match = _AFTER_COURSE_RE.search(source_text)
    if match:
        amount = int(match.group("amount"))
        multiplier = {"시간": 1, "일": 24, "주": 7 * 24}[match.group("unit")]
        side = _source_side(match.group("subject"), left_ingredient, right_ingredient)
        if side:
            return {
                "status": "structured",
                "kind": "washout_after",
                "hours": amount * multiplier,
                "amount": amount,
                "unit": match.group("unit"),
                "source_side": side,
                "subject": match.group("subject").strip(),
                "source_text": source_text,
            }
        return {
            "status": "not_evaluable",
            "kind": "post_course_restriction",
            "reason": "washout source ingredient could not be resolved uniquely",
            "source_text": source_text,
        }
    if any(marker in source_text for marker in _POST_COURSE_MARKERS):
        return {
            "status": "not_evaluable",
            "kind": "post_course_restriction",
            "reason": "post-course restriction duration could not be resolved",
            "source_text": source_text,
        }
    return {"status": "default", "kind": "course_overlap", "source_text": source_text}


def _course(value: Mapping[str, Any]) -> tuple[date, date | None] | None:
    raw_start = value.get("start_date")
    if not raw_start:
        return None
    try:
        start = date.fromisoformat(str(raw_start))
        end = date.fromisoformat(str(value["end_date"])) if value.get("end_date") else None
        stopped = (
            date.fromisoformat(str(value["stopped_at"]))
            if not value.get("active", True) and value.get("stopped_at")
            else None
        )
    except (TypeError, ValueError):
        return None
    if end is not None and end < start:
        return None
    if stopped is not None:
        if stopped < start:
            return None
        end = stopped if end is None else min(end, stopped)
    return start, end


def courses_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool | None:
    left_course = _course(left)
    right_course = _course(right)
    if left_course is None or right_course is None:
        return None
    left_start, left_end = left_course
    right_start, right_end = right_course
    if left_end is not None and left_end < right_start:
        return False
    if right_end is not None and right_end < left_start:
        return False
    return True


def _potential_gap_within_hours(
    first: tuple[date, date | None],
    second: tuple[date, date | None],
    hours: int,
) -> bool:
    first_start, first_end = first
    second_start, second_end = second
    if first_end is not None and first_end < second_start:
        calendar_days = (second_start - first_end).days
        return max(calendar_days - 1, 0) * 24 < hours
    if second_end is not None and second_end < first_start:
        calendar_days = (first_start - second_end).days
        return max(calendar_days - 1, 0) * 24 < hours
    return True


def interaction_timing_applies(
    timing: Mapping[str, Any],
    candidate_course: Mapping[str, Any],
    current_course: Mapping[str, Any],
    *,
    candidate_side: str,
) -> bool:
    """Return whether a matching DUR pair is temporally relevant.

    Prescription dates have day precision, not administration timestamps. For
    hour-based rules a boundary calendar day remains potentially inside the
    stated interval and therefore stays flagged.
    """
    overlap = courses_overlap(candidate_course, current_course)
    if overlap is None:
        return True
    if overlap:
        return True
    kind = timing.get("kind")
    if timing.get("status") == "not_evaluable" and kind == "post_course_restriction":
        return True
    if kind == "course_overlap":
        return False
    candidate = _course(candidate_course)
    current = _course(current_course)
    if candidate is None or current is None:
        return True
    if kind == "minimum_separation":
        return _potential_gap_within_hours(candidate, current, int(timing["hours"]))
    if kind == "washout_after":
        row_left = candidate if candidate_side == "left" else current
        row_right = current if candidate_side == "left" else candidate
        source = row_left if timing.get("source_side") == "left" else row_right
        target = row_right if timing.get("source_side") == "left" else row_left
        source_start, source_end = source
        target_start, target_end = target
        if source_end is None:
            return not (target_end is not None and target_end < source_start)
        if target_start > source_end:
            return _potential_gap_within_hours(source, target, int(timing["hours"]))
        return False
    return False


__all__ = ["courses_overlap", "interaction_timing_applies", "parse_interaction_timing"]
