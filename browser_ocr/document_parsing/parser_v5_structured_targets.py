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
    synthetic_negative: bool = False


@dataclass(frozen=True)
class ParserV5StructuredTargets:
    candidate_targets: tuple[float, ...]
    candidate_mask: tuple[float, ...]
    product_slots: tuple[ProductSlotTarget, ...]
    negative_product_node_indices: tuple[int, ...]
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
    labeled_roles_by_node: list[set[str]] = []
    ambiguous_by_node: list[bool] = []

    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            raise ValueError("Parser v5 observation node must be an object")
        targets = raw_node.get("targets")
        if not isinstance(targets, list):
            raise ValueError("Parser v5 observation targets must be a list")
        labeled_roles: set[str] = set()
        has_ambiguous = False
        for target in targets:
            if not isinstance(target, Mapping):
                raise ValueError("Parser v5 observation target must be an object")
            if target.get("label_status") != "labeled":
                has_ambiguous = True
                continue
            role = str(target.get("semantic_role") or "")
            labeled_roles.add(role)
            if role != "product":
                continue
            group = target.get("association_group")
            if group in slot_index and node_index not in product_members[slot_index[str(group)]]:
                product_members[slot_index[str(group)]].append(node_index)
        labeled_roles_by_node.append(labeled_roles)
        ambiguous_by_node.append(has_ambiguous)

    product_slots = tuple(
        ProductSlotTarget(medication_id=medication_id, member_node_indices=tuple(product_members[index]))
        for index, medication_id in enumerate(medication_ids)
    )
    negative_product_node_indices = tuple(
        node_index
        for node_index, labeled_roles in enumerate(labeled_roles_by_node)
        if "product" not in labeled_roles and not ambiguous_by_node[node_index]
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

    # Inference proposes field-role instances from model outputs rather than
    # semantic truth. Train the structured head on generic NONE instances for
    # every unambiguous OCR node that has no true medication-field role, so a
    # role false-positive does not become an untrained assignment case.
    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            continue
        if ambiguous_by_node[node_index] or labeled_roles_by_node[node_index] & FIELD_ROLES:
            continue
        node_id = str(raw_node.get("node_id") or "")
        for role in sorted(FIELD_ROLES):
            field_spans.append(
                FieldSpanTarget(
                    node_index=node_index,
                    node_id=node_id,
                    source_span_id="",
                    semantic_role=role,
                    association_group=None,
                    target_slot_index=None,
                    none_target=True,
                    supervised=True,
                    synthetic_negative=True,
                )
            )

    return ParserV5StructuredTargets(
        candidate_targets=candidate_targets,
        candidate_mask=candidate_mask,
        product_slots=product_slots,
        negative_product_node_indices=negative_product_node_indices,
        field_spans=tuple(field_spans),
    )


__all__ = [
    "FIELD_ROLES",
    "FieldSpanTarget",
    "ParserV5StructuredTargets",
    "ProductSlotTarget",
    "build_parser_v5_structured_targets",
]