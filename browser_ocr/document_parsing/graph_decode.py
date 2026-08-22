from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .document_graph import ROLE_LABELS
from .draft_contract import normalize_parser_draft


_FIELD_ROLES = {"dose", "frequency", "duration", "instruction", "schedule"}
_PRODUCT_PREFIX = re.compile(r"^(?:약명|제품명|약품명|의약품명)\s*[:：]?\s*", re.I)
_NUMBER = r"(?P<value>\d+(?:\.\d+)?|\d+/\d+)"
_DOSE = re.compile(rf"{_NUMBER}\s*(?P<unit>정|캡슐|포|mL|ml|tablet|capsule)", re.I)
_PACKET_TABLET = re.compile(rf"{_NUMBER}\s*포\s*\(\s*정\s*\)", re.I)
_FREQUENCY = re.compile(r"(?P<value>\d+)\s*회")
_DURATION = re.compile(r"(?P<value>\d+)\s*일(?:분)?")


@dataclass(frozen=True)
class DecodeConfig:
    product_threshold: float = 0.75
    product_margin: float = 0.18
    field_threshold: float = 0.62
    field_margin: float = 0.10
    relation_threshold: float = 0.72
    relation_margin: float = 0.12

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


def _number(raw: str) -> int | float | None:
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        divisor = int(denominator)
        if divisor == 0:
            return None
        value = int(numerator) / divisor
    else:
        value = float(raw)
    return int(value) if float(value).is_integer() else float(value)


def _unit(raw: str) -> str:
    lowered = raw.lower()
    if lowered in {"정", "tablet"}:
        return "tablet"
    if lowered in {"캡슐", "capsule"}:
        return "capsule"
    if lowered == "포":
        return "packet"
    if lowered == "ml":
        return "mL"
    return raw


def _dose_values(text: str) -> dict[str, object]:
    compact = "".join(text.split())
    packet_tablet = _PACKET_TABLET.search(compact)
    if packet_tablet:
        amount = _number(packet_tablet.group("value"))
        return {"dose_amount": amount, "dosage_text": compact} if amount is not None else {}
    match = _DOSE.search(compact)
    if not match:
        return {}
    amount = _number(match.group("value"))
    if amount is None:
        return {}
    return {"dose_amount": amount, "dose_unit": _unit(match.group("unit"))}


def _frequency_values(text: str) -> dict[str, object]:
    match = _FREQUENCY.search("".join(text.split()))
    if not match:
        return {}
    value = int(match.group("value"))
    return {"frequency_per_day": value} if 1 <= value <= 24 else {}


def _duration_values(text: str) -> dict[str, object]:
    match = _DURATION.search("".join(text.split()))
    if not match:
        return {}
    value = int(match.group("value"))
    return {"prescription_days": value} if 1 <= value <= 3650 else {}


def _normalized_time(period: str, hour_value: str, minute_value: str | None) -> str | None:
    hour = int(hour_value)
    minute = int(minute_value or "0")
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        return None
    if period.upper() == "PM" or period == "오후":
        if hour < 12:
            hour += 12
    elif hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _schedule_times(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"(오전|오후)\s*(\d{1,2})(?:\s*시)?(?:\s*[:：]\s*(\d{2}))?", text, re.I):
        value = _normalized_time(match.group(1), match.group(2), match.group(3))
        if value:
            found.append(value)
    for match in re.finditer(r"\b(AM|PM)\s*(\d{1,2})(?:\s*[:：]\s*(\d{2}))?\b", text, re.I):
        value = _normalized_time(match.group(1), match.group(2), match.group(3))
        if value:
            found.append(value)
    if re.search(r"(?:복용|투약|투여|복약)\s*시간|schedule\s*times?", text, re.I):
        for match in re.finditer(r"(?:^|[^0-9])([01]?[0-9]|2[0-3])\s*[:：]\s*([0-5][0-9])(?![0-9])", text):
            found.append(f"{int(match.group(1)):02d}:{match.group(2)}")
    return sorted(set(found))


def _instruction_values(text: str) -> dict[str, object]:
    values: dict[str, object] = {}
    meal_candidates: list[str] = []
    if re.search(r"식사\s*(?:와|와 함께|중)|식중", text, re.I):
        meal_candidates.append("with_meal")
    if re.search(r"식후|식사\s*후", text, re.I):
        meal_candidates.append("after_meal")
    if re.search(r"식전|식사\s*전", text, re.I):
        meal_candidates.append("before_meal")
    if re.search(r"공복|빈속", text, re.I):
        meal_candidates.append("empty_stomach")
    if re.search(r"식사\s*(?:무관|관계\s*없이)", text, re.I):
        meal_candidates.append("regardless")
    if len(set(meal_candidates)) == 1:
        values["meal_relation"] = meal_candidates[0]

    route_patterns = (
        ("ophthalmic", r"점안|안약|ophthalm"),
        ("otic", r"점이|귀에|otic"),
        ("nasal", r"점비|비강|nasal"),
        ("inhaled", r"흡입|inhal"),
        ("injection", r"주사|inject"),
        ("topical", r"외용|도포|바르|연고|크림|겔|로션|topical"),
        ("oral", r"경구|복용|먹|oral"),
    )
    routes = [route for route, pattern in route_patterns if re.search(pattern, text, re.I)]
    if len(set(routes)) == 1:
        values["administration_route"] = routes[0]
    if re.search(r"필요\s*시|\bPRN\b|as\s+needed", text, re.I):
        values["as_needed"] = True
    times = _schedule_times(text)
    if times:
        values["schedule_times"] = times
    return values


def _typed_values(role: str, text: str) -> dict[str, object]:
    if role == "dose":
        return _dose_values(text)
    if role == "frequency":
        return _frequency_values(text)
    if role == "duration":
        return _duration_values(text)
    if role in {"instruction", "schedule"}:
        return _instruction_values(text)
    return {}


def _ranked_role(scores: Mapping[str, float]) -> tuple[str, float, float]:
    if set(scores) != set(ROLE_LABELS):
        raise ValueError("role scores must contain exactly the graph role labels")
    normalized: list[tuple[float, str]] = []
    for role in ROLE_LABELS:
        raw = scores[role]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("role scores must be numeric")
        value = float(raw)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("role scores must be between 0 and 1")
        normalized.append((value, role))
    normalized.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return normalized[0][1], normalized[0][0], normalized[1][0]


def _add_uncertainty(row: dict[str, Any], code: str) -> None:
    if code not in row["uncertainty_codes"]:
        row["uncertainty_codes"].append(code)


def decode_graph_scores(
    document: Mapping[str, Any],
    role_scores: Mapping[str, Mapping[str, float]],
    association_scores: Mapping[tuple[str, str], float],
    *,
    config: DecodeConfig = DecodeConfig(),
) -> list[dict[str, Any]]:
    observation = document.get("observation")
    if not isinstance(observation, Mapping) or not isinstance(observation.get("nodes"), list):
        raise ValueError("parser document observation nodes are required")
    raw_nodes = observation["nodes"]
    nodes: dict[str, Mapping[str, Any]] = {}
    order: dict[str, int] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping):
            raise ValueError("parser observation contains a non-object node")
        node_id = str(raw.get("node_id") or "")
        if not node_id or node_id in nodes:
            raise ValueError("parser observation node ids must be non-empty and unique")
        nodes[node_id] = raw
        order[node_id] = index
    if set(role_scores) != set(nodes):
        raise ValueError("role scores must cover every OCR node exactly once")

    predicted_roles: dict[str, tuple[str, float, float]] = {
        node_id: _ranked_role(role_scores[node_id]) for node_id in nodes
    }
    products = [
        node_id for node_id, (role, score, second) in predicted_roles.items()
        if role == "product" and score >= config.product_threshold and score - second >= config.product_margin
    ]
    products.sort(key=lambda node_id: order[node_id])
    rows: dict[str, dict[str, Any]] = {}
    for node_id in products:
        product_query = _PRODUCT_PREFIX.sub("", str(nodes[node_id].get("text") or "")).strip()
        if not product_query:
            continue
        rows[node_id] = {
            "row_id": node_id,
            "product_query": product_query,
            "draft": {},
            "uncertainty_codes": [],
            "evidence": {"product_query": [node_id]},
        }
    products = list(rows)
    if not products:
        return []

    assigned: dict[str, list[tuple[float, str, str]]] = {product: [] for product in products}
    for field_id, (role, role_score, second_role_score) in predicted_roles.items():
        if role not in _FIELD_ROLES or role_score < config.field_threshold or role_score - second_role_score < config.field_margin:
            continue
        ranked: list[tuple[float, str]] = []
        for product_id in products:
            raw_score = association_scores.get((product_id, field_id))
            if raw_score is None:
                raise ValueError(f"missing association score for {product_id}/{field_id}")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise ValueError("association scores must be numeric")
            score = float(raw_score)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError("association scores must be between 0 and 1")
            ranked.append((score, product_id))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, best_product = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score < config.relation_threshold:
            continue
        if best_score - second_score < config.relation_margin:
            _add_uncertainty(rows[best_product], "AMBIGUOUS_ASSOCIATION")
            continue
        assigned[best_product].append((best_score, role, field_id))

    for product_id, candidates in assigned.items():
        row = rows[product_id]
        draft: dict[str, object] = row["draft"]
        evidence: dict[str, list[str]] = row["evidence"]
        single_roles = {"dose", "frequency", "duration"}
        selected: list[tuple[float, str, str]] = []
        for role in single_roles:
            choices = [item for item in candidates if item[1] == role]
            if choices:
                selected.append(max(choices, key=lambda item: (item[0], item[2])))
        selected.extend(item for item in candidates if item[1] in {"instruction", "schedule"})
        for _, role, field_id in sorted(selected, key=lambda item: (order[item[2]], item[1])):
            values = _typed_values(role, str(nodes[field_id].get("text") or ""))
            if not values:
                _add_uncertainty(row, "UNPARSEABLE_FIELD")
                continue
            for field, value in values.items():
                if field in draft and draft[field] != value:
                    draft.pop(field, None)
                    evidence.pop(field, None)
                    _add_uncertainty(row, "CONFLICTING_REGIMEN")
                    continue
                draft[field] = value
                evidence.setdefault(field, []).append(field_id)

        if draft.get("as_needed") is True:
            for field in ("frequency_per_day", "schedule_times"):
                if field in draft:
                    draft.pop(field, None)
                    evidence.pop(field, None)
                    _add_uncertainty(row, "PRN_SUPPRESSED_FIXED_SCHEDULE")
        schedule = draft.get("schedule_times")
        frequency = draft.get("frequency_per_day")
        if isinstance(schedule, list) and schedule and isinstance(frequency, int) and len(schedule) != frequency:
            draft.pop("schedule_times", None)
            evidence.pop("schedule_times", None)
            _add_uncertainty(row, "CONFLICTING_REGIMEN")
        try:
            row["draft"] = normalize_parser_draft(draft)
        except ValueError:
            row["draft"] = {}
            row["evidence"] = {"product_query": [product_id]}
            _add_uncertainty(row, "CONFLICTING_REGIMEN")

    return [rows[product_id] for product_id in products]


__all__ = ["DecodeConfig", "decode_graph_scores"]