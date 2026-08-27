from __future__ import annotations

import math
from dataclasses import dataclass

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .parser_v5_model_input import (
    BYTE_PAD,
    BYTE_VOCAB_SIZE,
    NODE_SCALAR_DIM,
    RELATION_FEATURE_DIM,
    ParserV5DocumentInput,
)


@dataclass(frozen=True)
class ParserV5EncoderSpec:
    hidden_dim: int = 96
    text_embedding_dim: int = 32
    text_conv_dim: int = 48
    layers: int = 2
    heads: int = 4
    feedforward_multiplier: int = 2

    def __post_init__(self) -> None:
        if not 32 <= self.hidden_dim <= 256:
            raise ValueError("Parser v5 hidden_dim must be between 32 and 256")
        if not 8 <= self.text_embedding_dim <= 128:
            raise ValueError("Parser v5 text_embedding_dim must be between 8 and 128")
        if not 8 <= self.text_conv_dim <= 128:
            raise ValueError("Parser v5 text_conv_dim must be between 8 and 128")
        if not 1 <= self.layers <= 6:
            raise ValueError("Parser v5 layers must be between 1 and 6")
        if self.heads not in {1, 2, 4, 8} or self.hidden_dim % self.heads:
            raise ValueError("Parser v5 heads must divide hidden_dim")
        if not 1 <= self.feedforward_multiplier <= 4:
            raise ValueError("Parser v5 feedforward_multiplier must be between 1 and 4")


@dataclass(frozen=True)
class ParserV5DocumentTensors:
    token_ids: paddle.Tensor
    token_mask: paddle.Tensor
    node_scalars: paddle.Tensor
    relation_features: paddle.Tensor


def parser_v5_document_tensors(value: ParserV5DocumentInput) -> ParserV5DocumentTensors:
    node_count = len(value.node_ids)
    max_text_bytes = len(value.token_ids[0]) if value.token_ids else 0
    return ParserV5DocumentTensors(
        token_ids=paddle.to_tensor(value.token_ids, dtype="int64").reshape([node_count, max_text_bytes]),
        token_mask=paddle.to_tensor(value.token_mask, dtype="bool").reshape([node_count, max_text_bytes]),
        node_scalars=paddle.to_tensor(value.node_scalars, dtype="float32").reshape([node_count, NODE_SCALAR_DIM]),
        relation_features=paddle.to_tensor(value.relation_features, dtype="float32").reshape(
            [node_count, node_count, RELATION_FEATURE_DIM]
        ),
    )


class _ByteSequenceEncoder(nn.Layer):
    def __init__(self, spec: ParserV5EncoderSpec) -> None:
        super().__init__()
        self.embedding = nn.Embedding(BYTE_VOCAB_SIZE, spec.text_embedding_dim, padding_idx=BYTE_PAD)
        self.conv3 = nn.Conv1D(spec.text_embedding_dim, spec.text_conv_dim, kernel_size=3, padding=1)
        self.conv5 = nn.Conv1D(spec.text_embedding_dim, spec.text_conv_dim, kernel_size=5, padding=2)

    def forward(self, token_ids: paddle.Tensor, token_mask: paddle.Tensor) -> tuple[paddle.Tensor, paddle.Tensor]:
        embedded = self.embedding(token_ids).transpose([0, 2, 1])
        encoded = paddle.concat([F.gelu(self.conv3(embedded)), F.gelu(self.conv5(embedded))], axis=1)
        mask = token_mask.astype(encoded.dtype).unsqueeze(1)
        denominator = paddle.clip(mask.sum(axis=2), min=1.0)
        mean_pool = (encoded * mask).sum(axis=2) / denominator
        masked = paddle.where(token_mask.unsqueeze(1), encoded, paddle.full_like(encoded, -1e4))
        max_pool = masked.max(axis=2)
        pooled = paddle.concat([mean_pool, max_pool], axis=1)
        return pooled, encoded.transpose([0, 2, 1])


class _DenseRelationAttentionLayer(nn.Layer):
    def __init__(self, spec: ParserV5EncoderSpec) -> None:
        super().__init__()
        self.hidden_dim = spec.hidden_dim
        self.heads = spec.heads
        self.head_dim = spec.hidden_dim // spec.heads
        self.qkv = nn.Linear(spec.hidden_dim, spec.hidden_dim * 3)
        self.relation_bias = nn.Linear(RELATION_FEATURE_DIM, spec.heads)
        self.relation_value = nn.Linear(RELATION_FEATURE_DIM, spec.hidden_dim)
        self.output = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.norm1 = nn.LayerNorm(spec.hidden_dim)
        feedforward_dim = spec.hidden_dim * spec.feedforward_multiplier
        self.feedforward = nn.Sequential(
            nn.Linear(spec.hidden_dim, feedforward_dim),
            nn.GELU(),
            nn.Linear(feedforward_dim, spec.hidden_dim),
        )
        self.norm2 = nn.LayerNorm(spec.hidden_dim)

    def forward(self, hidden: paddle.Tensor, relation_features: paddle.Tensor) -> paddle.Tensor:
        node_count = hidden.shape[0]
        if node_count == 0:
            return hidden
        qkv = self.qkv(hidden).reshape([node_count, 3, self.heads, self.head_dim]).transpose([1, 2, 0, 3])
        query, key, value = qkv[0], qkv[1], qkv[2]
        scores = paddle.matmul(query, key, transpose_y=True) / math.sqrt(self.head_dim)
        relation_bias = self.relation_bias(relation_features).transpose([2, 1, 0])
        attention = F.softmax(scores + relation_bias, axis=-1)
        node_context = paddle.matmul(attention, value)

        relation_value = self.relation_value(relation_features).reshape(
            [node_count, node_count, self.heads, self.head_dim]
        ).transpose([2, 1, 0, 3])
        relation_context = (attention.unsqueeze(-1) * relation_value).sum(axis=2)
        context = (node_context + relation_context).transpose([1, 0, 2]).reshape([node_count, self.hidden_dim])
        hidden = self.norm1(hidden + self.output(context))
        return self.norm2(hidden + self.feedforward(hidden))


class ParserV5DocumentEncoder(nn.Layer):
    """Runtime-visible OCR document encoder without semantic-role heads.

    The encoder preserves both contextual node states and position-sensitive
    text states. Direct row decoders can therefore reason globally while still
    pointing back into the original OCR text instead of treating a whole box
    as one semantic atom.
    """

    def __init__(self, spec: ParserV5EncoderSpec = ParserV5EncoderSpec()) -> None:
        super().__init__()
        self.spec = spec
        self.text_encoder = _ByteSequenceEncoder(spec)
        text_dim = spec.text_conv_dim * 4
        self.input_projection = nn.Linear(text_dim + NODE_SCALAR_DIM, spec.hidden_dim)
        self.layers = nn.LayerList([_DenseRelationAttentionLayer(spec) for _ in range(spec.layers)])

    def forward(self, tensors: ParserV5DocumentTensors) -> tuple[paddle.Tensor, paddle.Tensor]:
        text, token_states = self.text_encoder(tensors.token_ids, tensors.token_mask)
        hidden = F.gelu(self.input_projection(paddle.concat([text, tensors.node_scalars], axis=1)))
        for layer in self.layers:
            hidden = layer(hidden, tensors.relation_features)
        return hidden, token_states


def model_parameter_count(model: nn.Layer) -> int:
    return sum(int(parameter.numel()) for parameter in model.parameters())


__all__ = [
    "ParserV5DocumentEncoder",
    "ParserV5DocumentTensors",
    "ParserV5EncoderSpec",
    "model_parameter_count",
    "parser_v5_document_tensors",
]