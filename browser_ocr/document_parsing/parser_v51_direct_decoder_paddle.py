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
    """Scaled categorical pointer similarity, analogous to attention logits."""

    return paddle.matmul(query, key, transpose_y=True) / math.sqrt(hidden_dim)


@dataclass(frozen=True)
class ParserV51DecoderSpec:
    hidden_dim: int = 96
    text_token_dim: int = 96
    max_rows: int = 8
    max_field_pieces: int = 2
    feedforward_multiplier: int = 2

    def __post_init__(self) -> None:
        if not 32 <= self.hidden_dim <= 256:
            raise ValueError("Parser v5.1 decoder hidden_dim must be between 32 and 256")
        if not 16 <= self.text_token_dim <= 256:
            raise ValueError("Parser v5.1 decoder text_token_dim must be between 16 and 256")
        if not 1 <= self.max_rows <= 16:
            raise ValueError("Parser v5.1 decoder max_rows must be between 1 and 16")
        if not 1 <= self.max_field_pieces <= 8:
            raise ValueError("Parser v5.1 decoder max_field_pieces must be between 1 and 8")
        if not 1 <= self.feedforward_multiplier <= 4:
            raise ValueError("Parser v5.1 decoder feedforward_multiplier must be between 1 and 4")


@dataclass(frozen=True)
class ParserV51DecoderOutput:
    row_existence_logits: paddle.Tensor
    piece_node_logits: paddle.Tensor
    piece_start_logits: paddle.Tensor
    piece_end_logits: paddle.Tensor


class ParserV51DirectRowDecoder(nn.Layer):
    """DETR-like rows whose fields point directly at OCR byte evidence.

    Each row/field has a small fixed set of evidence-piece queries. Every piece
    first chooses one OCR node or NONE categorically, then predicts start/end
    byte positions inside the chosen node. This factorization keeps sparse
    extraction threshold-free without forcing one softmax to discriminate
    thousands of document-wide byte positions at once. Split OCR fragments
    occupy separate piece slots and are concatenated by the runtime decoder.
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
        self.piece_embedding = nn.Embedding(spec.max_field_pieces, spec.hidden_dim)
        self.node_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.pointer_node_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.product_anchor_projection = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.none_node = self.create_parameter(
            shape=[1, spec.hidden_dim],
            default_initializer=nn.initializer.Normal(std=0.02),
        )
        self.start_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.end_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
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

        field_embeddings = self.field_embedding.weight.reshape([1, len(ROW_FIELD_ROLES), 1, self.spec.hidden_dim])
        piece_embeddings = self.piece_embedding.weight.reshape([1, 1, self.spec.max_field_pieces, self.spec.hidden_dim])
        base_piece_hidden = row_hidden.reshape([self.spec.max_rows, 1, 1, self.spec.hidden_dim])
        base_piece_hidden = base_piece_hidden + field_embeddings + piece_embeddings

        # The product is the identity anchor of a medication row. Let the
        # first product piece softly select a product node, then expose that
        # product context to the remaining field/piece queries. This is fully
        # truth-free at runtime and avoids asking dose/frequency/etc. queries
        # to rediscover the row identity independently from scratch.
        node_count = node_hidden.shape[0]
        if node_count:
            product_query = self.node_query(base_piece_hidden[:, 0, 0, :])
            product_scores = scaled_pointer_scores(
                product_query,
                self.pointer_node_key(node_hidden),
                hidden_dim=self.spec.hidden_dim,
            )
            product_attention = F.softmax(product_scores, axis=1)
            product_anchor = paddle.matmul(product_attention, node_hidden)
        else:
            product_anchor = paddle.zeros_like(row_hidden)
        projected_anchor = self.product_anchor_projection(product_anchor).reshape(
            [self.spec.max_rows, 1, 1, self.spec.hidden_dim]
        )
        anchor_mask = paddle.ones(
            [1, len(ROW_FIELD_ROLES), self.spec.max_field_pieces, 1],
            dtype=base_piece_hidden.dtype,
        )
        anchor_mask[:, 0, 0, :] = 0.0
        piece_hidden = base_piece_hidden + projected_anchor * anchor_mask
        node_queries = self.node_query(piece_hidden)
        start_queries = self.start_query(piece_hidden)
        end_queries = self.end_query(piece_hidden)

        node_choices = paddle.concat([self.pointer_node_key(node_hidden), self.none_node], axis=0)
        piece_node_logits = scaled_pointer_scores(
            node_queries.reshape([-1, self.spec.hidden_dim]),
            node_choices,
            hidden_dim=self.spec.hidden_dim,
        ).reshape(
            [self.spec.max_rows, len(ROW_FIELD_ROLES), self.spec.max_field_pieces, node_count + 1]
        )
        text_length = tensors.token_ids.shape[1]
        shape = [
            self.spec.max_rows,
            len(ROW_FIELD_ROLES),
            self.spec.max_field_pieces,
            node_count,
            text_length,
        ]
        if node_count and text_length:
            contextual_tokens = paddle.concat(
                [token_states, node_hidden.unsqueeze(1).expand([-1, text_length, -1])],
                axis=2,
            )
            start_keys = self.start_key(contextual_tokens).reshape([node_count * text_length, self.spec.hidden_dim])
            end_keys = self.end_key(contextual_tokens).reshape([node_count * text_length, self.spec.hidden_dim])
            flat_start_queries = start_queries.reshape([-1, self.spec.hidden_dim])
            flat_end_queries = end_queries.reshape([-1, self.spec.hidden_dim])
            piece_start_logits = scaled_pointer_scores(
                flat_start_queries,
                start_keys,
                hidden_dim=self.spec.hidden_dim,
            ).reshape(shape)
            piece_end_logits = scaled_pointer_scores(
                flat_end_queries,
                end_keys,
                hidden_dim=self.spec.hidden_dim,
            ).reshape(shape)
            valid_byte = tensors.token_ids >= BYTE_OFFSET
            invalid = (~valid_byte).astype(piece_start_logits.dtype).reshape([1, 1, 1, node_count, text_length])
            piece_start_logits = piece_start_logits - invalid * 1e4
            piece_end_logits = piece_end_logits - invalid * 1e4
        else:
            piece_start_logits = paddle.zeros(shape, dtype=piece_hidden.dtype)
            piece_end_logits = paddle.zeros(shape, dtype=piece_hidden.dtype)

        return ParserV51DecoderOutput(
            row_existence_logits=row_existence_logits,
            piece_node_logits=piece_node_logits,
            piece_start_logits=piece_start_logits,
            piece_end_logits=piece_end_logits,
        )


@dataclass(frozen=True)
class ParserV51DecodeConfig:
    row_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.row_threshold) <= 1.0:
            raise ValueError("Parser v5.1 row_threshold must be in [0, 1]")


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
    node_classes = output.piece_node_logits.numpy()
    start_logits = output.piece_start_logits.numpy()
    end_logits = output.piece_end_logits.numpy()
    node_count = len(nodes)
    text_length = int(output.piece_start_logits.shape[-1]) if node_count else 0
    rows: list[dict[str, Any]] = []
    for row_index, score in enumerate(existence.tolist()):
        if float(score) < config.row_threshold:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            evidence: list[dict[str, Any]] = []
            for piece_index in range(int(output.piece_start_logits.shape[2])):
                node_index = int(node_classes[row_index, field_index, piece_index].argmax())
                if node_index == node_count or text_length == 0:
                    continue
                if node_index >= node_count:
                    continue
                start_token = int(start_logits[row_index, field_index, piece_index, node_index].argmax())
                end_token = int(end_logits[row_index, field_index, piece_index, node_index].argmax())
                text = _selected_text(str(nodes[node_index]["text"]), start_token, end_token)
                if text is None:
                    continue
                evidence.append(
                    {
                        "text": text,
                        "node_id": str(nodes[node_index]["node_id"]),
                        "piece_slot": piece_index,
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
    "scaled_pointer_scores",
]
