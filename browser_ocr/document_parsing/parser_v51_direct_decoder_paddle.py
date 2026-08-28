from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .parser_v5_document_encoder_paddle import ParserV5DocumentTensors
from .parser_v5_model_input import BYTE_OFFSET
from .parser_v51_targets import ROW_FIELD_ROLES


@dataclass(frozen=True)
class ParserV51DecoderSpec:
    hidden_dim: int = 96
    text_token_dim: int = 96
    max_rows: int = 8
    feedforward_multiplier: int = 2

    def __post_init__(self) -> None:
        if not 32 <= self.hidden_dim <= 256:
            raise ValueError("Parser v5.1 decoder hidden_dim must be between 32 and 256")
        if not 16 <= self.text_token_dim <= 256:
            raise ValueError("Parser v5.1 decoder text_token_dim must be between 16 and 256")
        if not 1 <= self.max_rows <= 16:
            raise ValueError("Parser v5.1 decoder max_rows must be between 1 and 16")
        if not 1 <= self.feedforward_multiplier <= 4:
            raise ValueError("Parser v5.1 decoder feedforward_multiplier must be between 1 and 4")


@dataclass(frozen=True)
class ParserV51DecoderOutput:
    row_existence_logits: paddle.Tensor
    field_presence_logits: paddle.Tensor
    field_node_logits: paddle.Tensor
    field_start_logits: paddle.Tensor
    field_end_logits: paddle.Tensor


class ParserV51DirectRowDecoder(nn.Layer):
    """DETR-like medication rows with direct multi-node OCR span membership.

    No header/context/other classifier exists on this path. Each row query
    predicts which output fields are present, which OCR nodes contribute to
    each field, and the byte span used from every selected node. Unselected OCR
    text is naturally unused. Multi-node membership lets a split product or
    regimen field be reconstructed without first inventing semantic node
    classes.
    """

    def __init__(self, spec: ParserV51DecoderSpec = ParserV51DecoderSpec()) -> None:
        super().__init__()
        self.spec = spec
        self.row_queries = self.create_parameter(
            shape=[spec.max_rows, spec.hidden_dim],
            default_initializer=nn.initializer.Normal(std=0.02),
        )
        self.row_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.node_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.node_value = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.row_norm1 = nn.LayerNorm(spec.hidden_dim)
        feedforward_dim = spec.hidden_dim * spec.feedforward_multiplier
        self.row_feedforward = nn.Sequential(
            nn.Linear(spec.hidden_dim, feedforward_dim),
            nn.GELU(),
            nn.Linear(feedforward_dim, spec.hidden_dim),
        )
        self.row_norm2 = nn.LayerNorm(spec.hidden_dim)
        self.row_existence = nn.Linear(spec.hidden_dim, 1)
        self.field_embedding = nn.Embedding(len(ROW_FIELD_ROLES), spec.hidden_dim)
        self.field_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.field_presence = nn.Linear(spec.hidden_dim, 1)
        self.pointer_node_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        token_input_dim = spec.text_token_dim + spec.hidden_dim
        self.start_key = nn.Linear(token_input_dim, spec.hidden_dim)
        self.end_key = nn.Linear(token_input_dim, spec.hidden_dim)

    def _row_states(self, node_hidden: paddle.Tensor) -> paddle.Tensor:
        queries = self.row_queries
        node_count = node_hidden.shape[0]
        if node_count == 0:
            context = paddle.zeros_like(queries)
        else:
            scores = paddle.matmul(self.row_query(queries), self.node_key(node_hidden), transpose_y=True)
            scores = scores / math.sqrt(self.spec.hidden_dim)
            attention = F.softmax(scores, axis=1)
            context = paddle.matmul(attention, self.node_value(node_hidden))
        hidden = self.row_norm1(queries + context)
        return self.row_norm2(hidden + self.row_feedforward(hidden))

    def forward(
        self,
        node_hidden: paddle.Tensor,
        token_states: paddle.Tensor,
        tensors: ParserV5DocumentTensors,
    ) -> ParserV51DecoderOutput:
        row_hidden = self._row_states(node_hidden)
        row_existence_logits = self.row_existence(row_hidden).reshape([self.spec.max_rows])

        field_embeddings = self.field_embedding.weight.unsqueeze(0)
        field_hidden = row_hidden.unsqueeze(1) + field_embeddings
        field_queries = self.field_query(field_hidden)
        field_presence_logits = self.field_presence(field_hidden).reshape(
            [self.spec.max_rows, len(ROW_FIELD_ROLES)]
        )
        node_count = node_hidden.shape[0]
        field_count = len(ROW_FIELD_ROLES)
        if node_count:
            field_node_logits = paddle.matmul(
                field_queries.reshape([self.spec.max_rows * field_count, self.spec.hidden_dim]),
                self.pointer_node_key(node_hidden),
                transpose_y=True,
            ).reshape([self.spec.max_rows, field_count, node_count])
        else:
            field_node_logits = paddle.zeros(
                [self.spec.max_rows, field_count, 0],
                dtype=field_queries.dtype,
            )

        text_length = tensors.token_ids.shape[1]
        if node_count and text_length:
            contextual_tokens = paddle.concat(
                [
                    token_states,
                    node_hidden.unsqueeze(1).expand([-1, text_length, -1]),
                ],
                axis=2,
            )
            start_keys = self.start_key(contextual_tokens).reshape([node_count * text_length, self.spec.hidden_dim])
            end_keys = self.end_key(contextual_tokens).reshape([node_count * text_length, self.spec.hidden_dim])
            flat_queries = field_queries.reshape([self.spec.max_rows * field_count, self.spec.hidden_dim])
            start_logits = paddle.matmul(flat_queries, start_keys, transpose_y=True).reshape(
                [self.spec.max_rows, field_count, node_count, text_length]
            )
            end_logits = paddle.matmul(flat_queries, end_keys, transpose_y=True).reshape(
                [self.spec.max_rows, field_count, node_count, text_length]
            )
            text_mask = tensors.token_ids >= BYTE_OFFSET
            invalid = (~text_mask).astype(start_logits.dtype).reshape([1, 1, node_count, text_length])
            field_start_logits = start_logits - invalid * 1e4
            field_end_logits = end_logits - invalid * 1e4
        else:
            shape = [self.spec.max_rows, field_count, node_count, text_length]
            field_start_logits = paddle.zeros(shape, dtype=field_queries.dtype)
            field_end_logits = paddle.zeros(shape, dtype=field_queries.dtype)

        return ParserV51DecoderOutput(
            row_existence_logits=row_existence_logits,
            field_presence_logits=field_presence_logits,
            field_node_logits=field_node_logits,
            field_start_logits=field_start_logits,
            field_end_logits=field_end_logits,
        )


@dataclass(frozen=True)
class ParserV51DecodeConfig:
    row_threshold: float = 0.5
    field_threshold: float = 0.5
    node_threshold: float = 0.5

    def __post_init__(self) -> None:
        for name in ("row_threshold", "field_threshold", "node_threshold"):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"Parser v5.1 {name} must be in [0, 1]")


def _selected_text(text: str, start_token: int, end_token: int) -> str | None:
    if start_token < 1 or end_token < start_token:
        return None
    payload = text.encode("utf-8")
    start_byte = start_token - 1
    end_byte = end_token
    if start_byte >= len(payload) or end_byte > len(payload):
        return None
    try:
        selected = payload[start_byte:end_byte].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return selected if selected.strip() else None


@paddle.no_grad()
def decode_parser_v51_rows(
    *,
    nodes: Sequence[Mapping[str, Any]],
    output: ParserV51DecoderOutput,
    config: ParserV51DecodeConfig = ParserV51DecodeConfig(),
) -> list[dict[str, Any]]:
    existence = F.sigmoid(output.row_existence_logits).numpy()
    presence = F.sigmoid(output.field_presence_logits).numpy()
    membership = F.sigmoid(output.field_node_logits).numpy()
    start_logits = output.field_start_logits.numpy()
    end_logits = output.field_end_logits.numpy()
    node_count = len(nodes)
    rows: list[dict[str, Any]] = []
    for row_index, score in enumerate(existence.tolist()):
        if float(score) < config.row_threshold:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            if float(presence[row_index, field_index]) < config.field_threshold:
                continue
            selected_nodes = [
                node_index
                for node_index in range(node_count)
                if float(membership[row_index, field_index, node_index]) >= config.node_threshold
            ]
            evidence: list[dict[str, Any]] = []
            for node_index in selected_nodes:
                start_token = int(start_logits[row_index, field_index, node_index].argmax())
                end_token = int(end_logits[row_index, field_index, node_index].argmax())
                text = _selected_text(str(nodes[node_index]["text"]), start_token, end_token)
                if text is None:
                    continue
                evidence.append(
                    {
                        "text": text,
                        "node_id": str(nodes[node_index]["node_id"]),
                        "start_token": start_token,
                        "end_token": end_token,
                    }
                )
            if evidence:
                fields[role] = {
                    "text": "".join(item["text"] for item in evidence),
                    "evidence": evidence,
                }
        product = fields.pop("product", None)
        if product is None:
            continue
        rows.append(
            {
                "row_slot": row_index,
                "row_confidence": float(score),
                "product_query": product["text"],
                "product_evidence": product["evidence"],
                "fields": fields,
            }
        )
    return rows


__all__ = [
    "ParserV51DecodeConfig",
    "ParserV51DecoderOutput",
    "ParserV51DecoderSpec",
    "ParserV51DirectRowDecoder",
    "decode_parser_v51_rows",
]
