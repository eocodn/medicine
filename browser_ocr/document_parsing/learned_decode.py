from __future__ import annotations

import re
from typing import Mapping, Sequence

from .learned_features import (
    DOSE_VALUE,
    DURATION_VALUE,
    FREQUENCY_VALUE,
    PACKET_TABLET,
    LayoutNode,
    center,
    compact,
)


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


def _field_values(role: str, text: str) -> dict[str, object]:
    value = compact(text)
    if role == "dose":
        packet_tablet = PACKET_TABLET.fullmatch(value)
        if packet_tablet:
            amount = float(packet_tablet.group(1))
            return {
                "dose_amount": int(amount) if amount.is_integer() else amount,
                "dosage_text": value,
            }
        match = DOSE_VALUE.fullmatch(value)
        if match:
            amount = float(match.group(1))
            return {
                "dose_amount": int(amount) if amount.is_integer() else amount,
                "dose_unit": _unit(match.group(2)),
            }
    if role == "frequency" and (match := FREQUENCY_VALUE.fullmatch(value)):
        return {"frequency_per_day": int(match.group(1))}
    if role == "duration" and (match := DURATION_VALUE.fullmatch(value)):
        return {"prescription_days": int(match.group(1))}
    return {}


def _clean_product(text: str) -> str:
    value = compact(text)
    return re.sub(r"^(?:약명|제품명|약품명|의약품명)[:：]?", "", value).strip()


def assemble_rows(
    *,
    products: Sequence[LayoutNode],
    fields: Sequence[tuple[str, LayoutNode]],
    edge_scores: Mapping[tuple[str, str], float],
    edge_threshold: float,
    edge_margin: float,
) -> list[dict[str, object]]:
    ordered_products = sorted(products, key=lambda node: (center(node)[1], center(node)[0], node.box_id))
    rows: list[dict[str, object]] = []
    row_by_product: dict[str, dict[str, object]] = {}
    for product in ordered_products:
        product_query = _clean_product(product.text)
        if not product_query:
            continue
        row = {
            "row_id": product.box_id,
            "product_query": product_query,
            "draft": {},
            "uncertainty_codes": [],
            "evidence": {"product_query": [product.box_id]},
        }
        rows.append(row)
        row_by_product[product.box_id] = row

    selected: dict[tuple[str, str], tuple[float, LayoutNode]] = {}
    for role, field in fields:
        ranked = sorted(
            ((float(edge_scores.get((product.box_id, field.box_id), 0.0)), product) for product in ordered_products),
            key=lambda item: (item[0], item[1].box_id),
            reverse=True,
        )
        if not ranked:
            continue
        best_score, best_product = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score < edge_threshold or best_score - second_score < edge_margin:
            continue
        key = (best_product.box_id, role)
        previous = selected.get(key)
        if previous is None or best_score > previous[0]:
            selected[key] = (best_score, field)

    for (product_id, role), (_, field) in selected.items():
        row = row_by_product.get(product_id)
        if row is None:
            continue
        values = _field_values(role, field.text)
        if not values:
            continue
        draft = row["draft"]
        evidence = row["evidence"]
        assert isinstance(draft, dict) and isinstance(evidence, dict)
        for name, value in values.items():
            draft[name] = value
            evidence[name] = [field.box_id]
    return rows


__all__ = ["assemble_rows"]
