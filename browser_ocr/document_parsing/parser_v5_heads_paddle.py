from __future__ import annotations

from dataclasses import dataclass

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .parser_v5_model_input import RELATION_FEATURE_DIM
from .parser_v5_structured_targets import FIELD_ROLES, ParserV5StructuredTargets


FIELD_ROLE_LABELS = tuple(sorted(FIELD_ROLES))
_FIELD_ROLE_INDEX = {role: index for index, role in enumerate(FIELD_ROLE_LABELS)}


@dataclass(frozen=True)
class ParserV5HeadTargets:
    candidate_targets: paddle.Tensor
    candidate_mask: paddle.Tensor
    product_membership: paddle.Tensor
    product_available: paddle.Tensor
    field_node_index: paddle.Tensor
    field_role_index: paddle.Tensor
    assignment_targets: paddle.Tensor
    assignment_mask: paddle.Tensor
    assignment_positive_mask: paddle.Tensor
    assignment_none_mask: paddle.Tensor


def parser_v5_head_targets(value: ParserV5StructuredTargets, *, node_count: int) -> ParserV5HeadTargets:
    true_product_count = len(value.product_slots)
    product_count = true_product_count + len(value.negative_product_node_indices)
    membership = [[0.0] * node_count for _ in range(product_count)]
    available: list[bool] = []
    for slot_index, slot in enumerate(value.product_slots):
        members = slot.member_node_indices
        available.append(bool(members))
        if members:
            weight = 1.0 / len(members)
            for node_index in members:
                membership[slot_index][node_index] = weight
    for negative_offset, node_index in enumerate(value.negative_product_node_indices):
        slot_index = true_product_count + negative_offset
        membership[slot_index][node_index] = 1.0
        available.append(True)

    assignment_targets: list[int] = []
    assignment_mask: list[float] = []
    assignment_positive_mask: list[float] = []
    assignment_none_mask: list[float] = []
    field_node_index: list[int] = []
    field_role_index: list[int] = []
    none_index = product_count
    for field in value.field_spans:
        field_node_index.append(field.node_index)
        field_role_index.append(_FIELD_ROLE_INDEX[field.semantic_role])
        if field.none_target:
            assignment_targets.append(none_index)
        elif field.target_slot_index is not None:
            assignment_targets.append(field.target_slot_index)
        else:
            assignment_targets.append(0 if product_count else none_index)
        assignment_mask.append(1.0 if field.supervised else 0.0)
        assignment_positive_mask.append(1.0 if field.supervised and not field.none_target else 0.0)
        assignment_none_mask.append(1.0 if field.supervised and field.none_target else 0.0)

    field_count = len(value.field_spans)
    return ParserV5HeadTargets(
        candidate_targets=paddle.to_tensor(value.candidate_targets, dtype="float32").reshape([node_count]),
        candidate_mask=paddle.to_tensor(value.candidate_mask, dtype="float32").reshape([node_count]),
        product_membership=paddle.to_tensor(membership, dtype="float32").reshape([product_count, node_count]),
        product_available=paddle.to_tensor(available, dtype="bool").reshape([product_count]),
        field_node_index=paddle.to_tensor(field_node_index, dtype="int64").reshape([field_count]),
        field_role_index=paddle.to_tensor(field_role_index, dtype="int64").reshape([field_count]),
        assignment_targets=paddle.to_tensor(assignment_targets, dtype="int64").reshape([field_count]),
        assignment_mask=paddle.to_tensor(assignment_mask, dtype="float32").reshape([field_count]),
        assignment_positive_mask=paddle.to_tensor(assignment_positive_mask, dtype="float32").reshape([field_count]),
        assignment_none_mask=paddle.to_tensor(assignment_none_mask, dtype="float32").reshape([field_count]),
    )


class ParserV5SemanticAssignmentHead(nn.Layer):
    def __init__(self, *, hidden_dim: int, assignment_hidden_dim: int = 64, role_embedding_dim: int = 16) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.role_embedding = nn.Embedding(len(FIELD_ROLE_LABELS), role_embedding_dim)
        pair_dim = hidden_dim * 2 + role_embedding_dim + RELATION_FEATURE_DIM
        self.candidate_head = nn.Linear(hidden_dim, 1)
        self.assignment_hidden = nn.Linear(pair_dim, assignment_hidden_dim)
        self.assignment_output = nn.Linear(assignment_hidden_dim, 1)
        self.none_hidden = nn.Linear(hidden_dim + role_embedding_dim, assignment_hidden_dim)
        self.none_output = nn.Linear(assignment_hidden_dim, 1)

    def forward(
        self,
        hidden: paddle.Tensor,
        relation_features: paddle.Tensor,
        targets: ParserV5HeadTargets,
    ) -> tuple[paddle.Tensor, paddle.Tensor]:
        candidate_logits = self.candidate_head(hidden).reshape([-1])
        assignment_logits = self.score_assignments(
            hidden,
            relation_features,
            product_membership=targets.product_membership,
            product_available=targets.product_available,
            field_node_index=targets.field_node_index,
            field_role_index=targets.field_role_index,
        )
        return candidate_logits, assignment_logits

    def score_assignments(
        self,
        hidden: paddle.Tensor,
        relation_features: paddle.Tensor,
        *,
        product_membership: paddle.Tensor,
        product_available: paddle.Tensor,
        field_node_index: paddle.Tensor,
        field_role_index: paddle.Tensor,
    ) -> paddle.Tensor:
        """Score field-role instances against product slots plus NONE.

        The scoring path consumes only contextual node states, geometry and
        caller-provided candidate instances. Training targets are deliberately
        absent so the same head is usable during truth-free inference.
        """
        field_count = field_node_index.shape[0]
        product_count = product_membership.shape[0]
        if field_count == 0:
            return paddle.zeros([0, product_count + 1], dtype=hidden.dtype)

        field_hidden = paddle.gather(hidden, field_node_index, axis=0)
        role_hidden = self.role_embedding(field_role_index)
        none_logits = self.none_output(F.gelu(self.none_hidden(paddle.concat([field_hidden, role_hidden], axis=1))))
        if product_count == 0:
            return none_logits

        slot_hidden = paddle.matmul(product_membership, hidden)
        selected_relations = paddle.gather(relation_features, field_node_index, axis=0)
        pooled_relations = paddle.matmul(
            selected_relations.transpose([0, 2, 1]),
            product_membership.transpose([1, 0]),
        ).transpose([0, 2, 1])
        field_expanded = field_hidden.unsqueeze(1).expand([-1, product_count, -1])
        slot_expanded = slot_hidden.unsqueeze(0).expand([field_count, -1, -1])
        role_expanded = role_hidden.unsqueeze(1).expand([-1, product_count, -1])
        pair_input = paddle.concat([field_expanded, slot_expanded, role_expanded, pooled_relations], axis=2)
        product_logits = self.assignment_output(F.gelu(self.assignment_hidden(pair_input))).reshape([field_count, product_count])
        unavailable = (~product_available).astype(product_logits.dtype).reshape([1, product_count])
        product_logits = product_logits - unavailable * 1e4
        return paddle.concat([product_logits, none_logits], axis=1)


def parser_v5_head_loss(
    candidate_logits: paddle.Tensor,
    assignment_logits: paddle.Tensor,
    targets: ParserV5HeadTargets,
) -> paddle.Tensor:
    candidate = F.binary_cross_entropy_with_logits(candidate_logits, targets.candidate_targets, reduction="none")
    candidate_loss = (candidate * targets.candidate_mask).sum() / paddle.clip(targets.candidate_mask.sum(), min=1.0)
    if targets.assignment_targets.shape[0] == 0 or float(targets.assignment_mask.sum().item()) == 0.0:
        return candidate_loss
    assignment = F.cross_entropy(assignment_logits, targets.assignment_targets, reduction="none")
    assignment_components: list[paddle.Tensor] = []
    if float(targets.assignment_positive_mask.sum().item()) > 0.0:
        assignment_components.append(
            (assignment * targets.assignment_positive_mask).sum()
            / paddle.clip(targets.assignment_positive_mask.sum(), min=1.0)
        )
    if float(targets.assignment_none_mask.sum().item()) > 0.0:
        assignment_components.append(
            (assignment * targets.assignment_none_mask).sum()
            / paddle.clip(targets.assignment_none_mask.sum(), min=1.0)
        )
    assignment_loss = sum(assignment_components) / len(assignment_components)
    return candidate_loss + assignment_loss


__all__ = [
    "FIELD_ROLE_LABELS",
    "ParserV5HeadTargets",
    "ParserV5SemanticAssignmentHead",
    "parser_v5_head_loss",
    "parser_v5_head_targets",
]