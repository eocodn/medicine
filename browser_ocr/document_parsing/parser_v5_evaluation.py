from __future__ import annotations

from typing import Any, Mapping, Sequence

from .parser_v5_decode import FIELD_ROLES
from .parser_v5_contract import validate_parser_v5_pair


def _node_groups(node: Mapping[str, Any], *, role: str | None = None) -> set[str]:
    groups: set[str] = set()
    targets = node.get("targets")
    if not isinstance(targets, list):
        return groups
    for target in targets:
        if not isinstance(target, Mapping) or target.get("label_status") != "labeled":
            continue
        if role is not None and target.get("semantic_role") != role:
            continue
        group = target.get("association_group")
        if group is not None:
            groups.add(str(group))
    return groups


def evaluate_parser_v5_rows(
    truth: Mapping[str, Any],
    observation: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int | float]:
    validate_parser_v5_pair(truth, observation)
    nodes = {str(node["node_id"]): node for node in observation["nodes"]}
    gold_medications = {str(item["medication_id"]): item for item in truth["medications"]}
    gold_fields: dict[tuple[str, str], str] = {}
    for span in truth["spans"]:
        group = span.get("association_group")
        role = str(span.get("semantic_role") or "")
        if group is not None and role in FIELD_ROLES:
            gold_fields[(str(group), role)] = str(span["text"])

    predicted_groups: set[str] = set()
    product_tp = product_fp = 0
    field_exact = field_false_exact = field_unresolved = 0
    cross_medication = invented_values = 0
    observed_texts = {str(node["text"]) for node in observation["nodes"]}
    predicted_fields: set[tuple[str, str]] = set()

    for row in rows:
        product_node = nodes.get(str(row.get("product_node_id") or ""))
        groups = _node_groups(product_node, role="product") if product_node is not None else set()
        group = next(iter(groups)) if len(groups) == 1 else None
        if group is not None and group in gold_medications and group not in predicted_groups:
            product_tp += 1
            predicted_groups.add(group)
        else:
            product_fp += 1
        fields = row.get("fields")
        if not isinstance(fields, Mapping):
            continue
        for role, raw in fields.items():
            if role not in FIELD_ROLES or not isinstance(raw, Mapping):
                continue
            text = str(raw.get("text") or "")
            if text not in observed_texts:
                invented_values += 1
            if group is None:
                field_false_exact += 1
                continue
            predicted_fields.add((group, str(role)))
            expected = gold_fields.get((group, str(role)))
            if expected is not None and text == expected:
                field_exact += 1
            else:
                field_false_exact += 1
            evidence = nodes.get(str(raw.get("node_id") or ""))
            evidence_groups = _node_groups(evidence, role=str(role)) if evidence is not None else set()
            if evidence_groups and group not in evidence_groups:
                cross_medication += 1

    for key in gold_fields:
        if key not in predicted_fields:
            field_unresolved += 1

    product_fn = len(gold_medications) - product_tp
    precision = product_tp / max(product_tp + product_fp, 1)
    recall = product_tp / max(product_tp + product_fn, 1)
    return {
        "gold_rows": len(gold_medications),
        "predicted_rows": len(rows),
        "product_tp": product_tp,
        "product_fp": product_fp,
        "product_fn": product_fn,
        "product_precision": precision,
        "product_recall": recall,
        "field_total": len(gold_fields),
        "field_exact": field_exact,
        "field_false_exact": field_false_exact,
        "field_unresolved": field_unresolved,
        "cross_medication_associations": cross_medication,
        "invented_values": invented_values,
        "zero_medication_false_rows": len(rows) if not gold_medications else 0,
    }


__all__ = ["evaluate_parser_v5_rows"]