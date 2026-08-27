from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .parser_v5_contract import validate_parser_v5_pair


FIELD_ROLES = {"dose", "frequency", "duration", "instruction", "schedule"}


@dataclass(frozen=True)
class ProductSlotTarget:
    medication_id: str
    member_node_indices: tuple[int, ...]


@dataclass(frozen=True)
class FieldSpanTarget:
    node_index: int
    node_id: str
    source_span_id: str
    semantic_role: str
    association_group: str | None
    target_slot_index: int | None
    none_target: bool
    supervised: bool


@dataclass(frozen=True)
class ParserV5StructuredTargets:
    candidate_targets: tuple[float, ...]
    candidate_mask: tuple[float, ...]
    product_slots: tuple[ProductSlotTarget, ...]
    field_spans: tuple[FieldSpanTarget, ...]


def build_parser_v5_structured_targets(
    document: Mapping[str, object],
    observation: Mapping[str, object],
) -> ParserV5StructuredTargets:
    validate_parser_v5_pair(document, observation)  # type: ignore[arg-type]
    medications = document["medications"]
    raw_nodes = observation["nodes"]
    if not isinstance(medications, list) or not isinstance(raw_nodes, list):
        raise ValueError("Parser v5 structured targets require medication and observation lists")

    medication_ids = [str(medication["medication_id"]) for medication in medications]
    slot_index = {medication_id: index for index, medication_id in enumerate(medication_ids)}
    product_members: list[list[int]] = [[] for _ in medication_ids]

    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            raise ValueError("Parser v5 observation node must be an object")
        targets = raw_node.get("targets")
        if not isinstance(targets, list):
            raise ValueError("Parser v5 observation targets must be a list")
        for target in targets:
            if not isinstance(target, Mapping):
                raise ValueError("Parser v5 observation target must be an object")
            if target.get("label_status") != "labeled" or target.get("semantic_role") != "product":
                continue
            group = target.get("association_group")
            if group in slot_index and node_index not in product_members[slot_index[str(group)]]:
                product_members[slot_index[str(group)]].append(node_index)

    product_slots = tuple(
        ProductSlotTarget(medication_id=medication_id, member_node_indices=tuple(product_members[index]))
        for index, medication_id in enumerate(medication_ids)
    )

    candidate_targets = tuple(
        1.0 if isinstance(node, Mapping) and bool(node.get("source_span_ids")) else 0.0
        for node in raw_nodes
    )
    candidate_mask = tuple(1.0 for _ in raw_nodes)

    field_spans: list[FieldSpanTarget] = []
    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            continue
        targets = raw_node.get("targets")
        if not isinstance(targets, list):
            continue
        node_id = str(raw_node.get("node_id") or "")
        for target in targets:
            if not isinstance(target, Mapping):
                continue
            role = str(target.get("semantic_role") or "")
            if role not in FIELD_ROLES:
                continue
            group = target.get("association_group")
            labeled = target.get("label_status") == "labeled"
            if group is None:
                target_index = None
                none_target = labeled
                supervised = labeled
            else:
                target_index = slot_index.get(str(group))
                none_target = False
                supervised = (
                    labeled
                    and target_index is not None
                    and bool(product_slots[target_index].member_node_indices)
                )
            field_spans.append(
                FieldSpanTarget(
                    node_index=node_index,
                    node_id=node_id,
                    source_span_id=str(target.get("source_span_id") or ""),
                    semantic_role=role,
                    association_group=str(group) if group is not None else None,
                    target_slot_index=target_index,
                    none_target=none_target,
                    supervised=supervised,
                )
            )

    return ParserV5StructuredTargets(
        candidate_targets=candidate_targets,
        candidate_mask=candidate_mask,
        product_slots=product_slots,
        field_spans=tuple(field_spans),
    )


__all__ = [
    "FIELD_ROLES",
    "FieldSpanTarget",
    "ParserV5StructuredTargets",
    "ProductSlotTarget",
    "build_parser_v5_structured_targets",
]