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


def scaled_pointer_scores(query: paddle.Tensor, key: paddle.Tensor, *, hidden_dim: int) -> paddle.Tensor:
    """Scaled query/key similarity, analogous to attention logits."""

    return paddle.matmul(query, key, transpose_y=True) / math.sqrt(hidden_dim)


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
    field_token_logits: paddle.Tensor
    token_valid_mask: paddle.Tensor


class ParserV51DirectRowDecoder(nn.Layer):
    """Direct row decoder with variable-cardinality field segmentation.

    Every row/field query labels every runtime-visible OCR payload byte as
    OUTSIDE or SELECTED. Disjoint spans and an arbitrary number of OCR
    fragments therefore require no fixed fragment slots. Header/context text
    is not classified separately; it is simply left OUTSIDE by every output
    field.
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
        self.selected_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.outside_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        token_input_dim = spec.text_token_dim + spec.hidden_dim
        self.selected_key = nn.Linear(token_input_dim, spec.hidden_dim)
        self.outside_key = nn.Linear(token_input_dim, spec.hidden_dim)

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
        field_hidden = row_hidden.unsqueeze(1) + self.field_embedding.weight.unsqueeze(0)

        node_count = int(node_hidden.shape[0])
        text_length = int(tensors.token_ids.shape[1]) if tensors.token_ids.ndim == 2 else 0
        token_valid_mask = tensors.token_ids >= BYTE_OFFSET
        shape = [self.spec.max_rows, len(ROW_FIELD_ROLES), node_count, text_length]
        if node_count and text_length:
            contextual_tokens = paddle.concat(
                [token_states, node_hidden.unsqueeze(1).expand([-1, text_length, -1])],
                axis=2,
            )
            flat_selected_key = self.selected_key(contextual_tokens).reshape(
                [node_count * text_length, self.spec.hidden_dim]
            )
            flat_outside_key = self.outside_key(contextual_tokens).reshape(
                [node_count * text_length, self.spec.hidden_dim]
            )
            flat_selected_query = self.selected_query(field_hidden).reshape([-1, self.spec.hidden_dim])
            flat_outside_query = self.outside_query(field_hidden).reshape([-1, self.spec.hidden_dim])
            selected_logits = scaled_pointer_scores(
                flat_selected_query,
                flat_selected_key,
                hidden_dim=self.spec.hidden_dim,
            ).reshape(shape)
            outside_logits = scaled_pointer_scores(
                flat_outside_query,
                flat_outside_key,
                hidden_dim=self.spec.hidden_dim,
            ).reshape(shape)
            invalid = (~token_valid_mask).astype(selected_logits.dtype).reshape([1, 1, node_count, text_length])
            selected_logits = selected_logits - invalid * 1e4
            outside_logits = outside_logits * (1.0 - invalid)
            field_token_logits = paddle.stack([outside_logits, selected_logits], axis=-1)
        else:
            field_token_logits = paddle.zeros([*shape, 2], dtype=row_hidden.dtype)

        return ParserV51DecoderOutput(
            row_existence_logits=row_existence_logits,
            field_token_logits=field_token_logits,
            token_valid_mask=token_valid_mask,
        )


@dataclass(frozen=True)
class ParserV51DecodeConfig:
    row_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.row_threshold) <= 1.0:
            raise ValueError("Parser v5.1 row_threshold must be in [0, 1]")


def _selected_characters(text: str, token_logits) -> list[bool]:
    payload_offset = 0
    selected: list[bool] = []
    token_count = int(token_logits.shape[0])
    for char in text:
        byte_count = len(char.encode("utf-8"))
        start = payload_offset + 1
        end = payload_offset + byte_count + 1
        payload_offset += byte_count
        if start >= token_count or end > token_count:
            selected.append(False)
            continue
        margin = token_logits[start:end, 1] - token_logits[start:end, 0]
        selected.append(float(margin.mean()) >= 0.0)
    return selected


def _selected_segments(node: Mapping[str, Any], token_logits) -> list[dict[str, Any]]:
    text = str(node["text"])
    selected = _selected_characters(text, token_logits)
    result: list[dict[str, Any]] = []
    start: int | None = None
    for index in range(len(selected) + 1):
        active = index < len(selected) and selected[index]
        if active and start is None:
            start = index
        if not active and start is not None:
            fragment = text[start:index]
            if fragment.strip():
                result.append(
                    {
                        "text": fragment,
                        "node_id": str(node["node_id"]),
                        "start_char": start,
                        "end_char": index,
                    }
                )
            start = None
    return result


@paddle.no_grad()
def decode_parser_v51_rows(
    *,
    nodes: Sequence[Mapping[str, Any]],
    output: ParserV51DecoderOutput,
    config: ParserV51DecodeConfig = ParserV51DecodeConfig(),
) -> list[dict[str, Any]]:
    existence = F.sigmoid(output.row_existence_logits).numpy()
    token_logits = output.field_token_logits.numpy()
    rows: list[dict[str, Any]] = []
    for row_index, score in enumerate(existence.tolist()):
        if float(score) < config.row_threshold:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            evidence: list[dict[str, Any]] = []
            for node_index, node in enumerate(nodes):
                evidence.extend(_selected_segments(node, token_logits[row_index, field_index, node_index]))
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
    "scaled_pointer_scores",
]
