from __future__ import annotations

from dataclasses import dataclass

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .parser_v5_document_encoder_paddle import (
    ParserV5DocumentEncoder,
    ParserV5DocumentTensors,
    ParserV5EncoderSpec,
    model_parameter_count,
    parser_v5_document_tensors,
)
from .parser_v5_model_input import PARSER_V5_ROLE_LABELS, ParserV5ModelInput


@dataclass(frozen=True)
class ParserV5Tensors(ParserV5DocumentTensors):
    role_targets: paddle.Tensor
    role_mask: paddle.Tensor


def parser_v5_tensors(value: ParserV5ModelInput) -> ParserV5Tensors:
    document = parser_v5_document_tensors(value)
    node_count = len(value.node_ids)
    return ParserV5Tensors(
        token_ids=document.token_ids,
        token_mask=document.token_mask,
        node_scalars=document.node_scalars,
        relation_features=document.relation_features,
        role_targets=paddle.to_tensor(value.role_targets, dtype="float32").reshape(
            [node_count, len(PARSER_V5_ROLE_LABELS)]
        ),
        role_mask=paddle.to_tensor(value.role_mask, dtype="float32").reshape(
            [node_count, len(PARSER_V5_ROLE_LABELS)]
        ),
    )


class ParserV5GlobalEncoder(ParserV5DocumentEncoder):
    """Frozen-v5 compatibility wrapper adding the retired node-role head."""

    def __init__(self, spec: ParserV5EncoderSpec = ParserV5EncoderSpec()) -> None:
        super().__init__(spec)
        self.role_head = nn.Linear(spec.hidden_dim, len(PARSER_V5_ROLE_LABELS))

    def forward(self, tensors: ParserV5Tensors) -> tuple[paddle.Tensor, paddle.Tensor]:
        hidden, _ = super().forward(tensors)
        return hidden, self.role_head(hidden)


def masked_multilabel_role_loss(role_logits: paddle.Tensor, tensors: ParserV5Tensors) -> paddle.Tensor:
    elementwise = F.binary_cross_entropy_with_logits(role_logits, tensors.role_targets, reduction="none")
    positive_mask = tensors.role_mask * tensors.role_targets
    negative_mask = tensors.role_mask * (1.0 - tensors.role_targets)
    components: list[paddle.Tensor] = []
    if float(positive_mask.sum().item()) > 0.0:
        components.append((elementwise * positive_mask).sum() / paddle.clip(positive_mask.sum(), min=1.0))
    if float(negative_mask.sum().item()) > 0.0:
        components.append((elementwise * negative_mask).sum() / paddle.clip(negative_mask.sum(), min=1.0))
    if not components:
        return paddle.zeros([], dtype=role_logits.dtype)
    return sum(components) / len(components)


__all__ = [
    "ParserV5EncoderSpec",
    "ParserV5GlobalEncoder",
    "ParserV5Tensors",
    "masked_multilabel_role_loss",
    "model_parameter_count",
    "parser_v5_tensors",
]
