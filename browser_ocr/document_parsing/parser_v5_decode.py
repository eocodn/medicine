from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


FIELD_ROLES = ("dose", "frequency", "duration", "instruction", "schedule")


@dataclass(frozen=True)
class ParserV5DecodeConfig:
    candidate_threshold: float = 0.55
    role_threshold: float = 0.60
    assignment_threshold: float = 0.55
    assignment_margin: float = 0.10

    def __post_init__(self) -> None:
        for name in ("candidate_threshold", "role_threshold", "assignment_threshold", "assignment_margin"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Parser v5 decode {name} must be in [0, 1]")


def select_parser_v5_instances(
    *,
    role_labels: Sequence[str],
    role_probabilities: Sequence[Sequence[float]],
    candidate_probabilities: Sequence[float],
    config: ParserV5DecodeConfig = ParserV5DecodeConfig(),
) -> tuple[tuple[int, ...], tuple[tuple[int, str], ...]]:
    if len(role_probabilities) != len(candidate_probabilities):
        raise ValueError("Parser v5 decode role/candidate node counts disagree")
    role_index = {role: index for index, role in enumerate(role_labels)}
    if "product" not in role_index or any(role not in role_index for role in FIELD_ROLES):
        raise ValueError("Parser v5 decode role labels are incomplete")
    product_nodes: list[int] = []
    field_instances: list[tuple[int, str]] = []
    for node_index, (probabilities, candidate) in enumerate(zip(role_probabilities, candidate_probabilities, strict=True)):
        if len(probabilities) != len(role_labels):
            raise ValueError("Parser v5 decode role probability width disagrees with labels")
        if float(candidate) < config.candidate_threshold:
            continue
        if float(probabilities[role_index["product"]]) >= config.role_threshold:
            product_nodes.append(node_index)
        for role in FIELD_ROLES:
            if float(probabilities[role_index[role]]) >= config.role_threshold:
                field_instances.append((node_index, role))
    return tuple(product_nodes), tuple(field_instances)


def decode_parser_v5_rows(
    *,
    nodes: Sequence[Mapping[str, Any]],
    product_node_indices: Sequence[int],
    field_instances: Sequence[tuple[int, str]],
    assignment_probabilities: Sequence[Sequence[float]],
    config: ParserV5DecodeConfig = ParserV5DecodeConfig(),
) -> list[dict[str, Any]]:
    product_count = len(product_node_indices)
    if len(field_instances) != len(assignment_probabilities):
        raise ValueError("Parser v5 decode field/assignment counts disagree")
    rows: list[dict[str, Any]] = []
    for node_index in product_node_indices:
        node = nodes[node_index]
        rows.append({
            "product_node_id": str(node["node_id"]),
            "product_query": str(node["text"]),
            "fields": {},
        })

    # A merged OCR node may be classified as multiple semantic field roles, but
    # without a predicted character span boundary its full OCR text is not a
    # safe typed value for either role. Keep those fields unresolved.
    roles_per_node: dict[int, int] = {}
    for node_index, _ in field_instances:
        roles_per_node[node_index] = roles_per_node.get(node_index, 0) + 1

    for (node_index, role), raw_probabilities in zip(field_instances, assignment_probabilities, strict=True):
        probabilities = [float(value) for value in raw_probabilities]
        if len(probabilities) != product_count + 1:
            raise ValueError("Parser v5 decode assignment width must be product slots plus NONE")
        if product_count == 0 or roles_per_node[node_index] != 1:
            continue
        ranked = sorted(range(len(probabilities)), key=lambda index: probabilities[index], reverse=True)
        winner = ranked[0]
        if winner == product_count:
            continue
        winner_score = probabilities[winner]
        runner_up = probabilities[ranked[1]] if len(ranked) > 1 else 0.0
        if winner_score < config.assignment_threshold or winner_score - runner_up < config.assignment_margin:
            continue
        node = nodes[node_index]
        existing = rows[winner]["fields"].get(role)
        value = {
            "text": str(node["text"]),
            "node_id": str(node["node_id"]),
            "confidence": winner_score,
        }
        if existing is None or winner_score > float(existing["confidence"]):
            rows[winner]["fields"][role] = value
    return rows


__all__ = [
    "FIELD_ROLES",
    "ParserV5DecodeConfig",
    "decode_parser_v5_rows",
    "select_parser_v5_instances",
]