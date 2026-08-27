from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import paddle
import paddle.nn.functional as F

from .parser_v5_decode import ParserV5DecodeConfig, decode_parser_v5_rows, select_parser_v5_instances
from .parser_v5_encoder_paddle import parser_v5_tensors
from .parser_v5_heads_paddle import FIELD_ROLE_LABELS
from .parser_v5_model_input import PARSER_V5_ROLE_LABELS, ParserV5ModelInput


@dataclass(frozen=True)
class ParserV5InferenceResult:
    rows: tuple[dict[str, Any], ...]
    product_node_indices: tuple[int, ...]
    field_instances: tuple[tuple[int, str], ...]
    role_probabilities: tuple[tuple[float, ...], ...]
    candidate_probabilities: tuple[float, ...]
    assignment_probabilities: tuple[tuple[float, ...], ...]


@paddle.no_grad()
def run_parser_v5_inference(
    *,
    encoder,
    heads,
    model_input: ParserV5ModelInput,
    nodes: Sequence[Mapping[str, Any]],
    config: ParserV5DecodeConfig = ParserV5DecodeConfig(),
) -> ParserV5InferenceResult:
    """Run Parser v5 without semantic truth or training-target objects."""

    if len(nodes) != len(model_input.node_ids):
        raise ValueError("Parser v5 inference node count disagrees with model input")
    tensors = parser_v5_tensors(model_input)
    hidden, role_logits = encoder(tensors)
    candidate_logits = heads.candidate_head(hidden).reshape([-1])
    role_probabilities = F.sigmoid(role_logits).numpy().tolist()
    candidate_probabilities = F.sigmoid(candidate_logits).numpy().reshape([-1]).tolist()
    product_nodes, field_instances = select_parser_v5_instances(
        role_labels=PARSER_V5_ROLE_LABELS,
        role_probabilities=role_probabilities,
        candidate_probabilities=candidate_probabilities,
        config=config,
    )

    node_count = len(nodes)
    product_count = len(product_nodes)
    membership = [[0.0] * node_count for _ in range(product_count)]
    for slot_index, node_index in enumerate(product_nodes):
        membership[slot_index][node_index] = 1.0
    role_index = {role: index for index, role in enumerate(FIELD_ROLE_LABELS)}
    field_node_index = [node_index for node_index, _ in field_instances]
    field_role_index = [role_index[role] for _, role in field_instances]
    assignment_logits = heads.score_assignments(
        hidden,
        tensors.relation_features,
        product_membership=paddle.to_tensor(membership, dtype="float32").reshape([product_count, node_count]),
        product_available=paddle.ones([product_count], dtype="bool"),
        field_node_index=paddle.to_tensor(field_node_index, dtype="int64").reshape([len(field_instances)]),
        field_role_index=paddle.to_tensor(field_role_index, dtype="int64").reshape([len(field_instances)]),
    )
    assignment_probabilities = (
        F.softmax(assignment_logits, axis=1).numpy().tolist() if len(field_instances) else []
    )
    rows = decode_parser_v5_rows(
        nodes=nodes,
        product_node_indices=product_nodes,
        field_instances=field_instances,
        assignment_probabilities=assignment_probabilities,
        config=config,
    )
    return ParserV5InferenceResult(
        rows=tuple(rows),
        product_node_indices=product_nodes,
        field_instances=field_instances,
        role_probabilities=tuple(tuple(float(value) for value in row) for row in role_probabilities),
        candidate_probabilities=tuple(float(value) for value in candidate_probabilities),
        assignment_probabilities=tuple(tuple(float(value) for value in row) for row in assignment_probabilities),
    )


__all__ = ["ParserV5InferenceResult", "run_parser_v5_inference"]