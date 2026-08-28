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
    field_query_states: paddle.Tensor
    node_pointer_keys: paddle.Tensor
    start_pointer_keys: paddle.Tensor
    end_pointer_keys: paddle.Tensor
    evidence_values: paddle.Tensor
    token_valid_mask: paddle.Tensor


class ParserV51DirectRowDecoder(nn.Layer):
    """Direct row decoder exposing memory for categorical evidence sequences.

    A document forward pass creates row/field query states plus OCR node/span
    key-value memories. Training and runtime then autoregressively choose one
    OCR node or STOP, choose start/end positions inside that node, fold the
    selected span value back into the field state, and repeat. Evidence
    cardinality is therefore runtime-determined rather than corpus-bounded.
    """

    def __init__(self, spec: ParserV51DecoderSpec = ParserV51DecoderSpec()) -> None:
        super().__init__()
        self.spec = spec
        self.row_queries = self.create_parameter(
            shape=[spec.max_rows, spec.hidden_dim],
            default_initializer=nn.initializer.Normal(std=0.02),
        )
        self.row_self_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.row_self_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.row_self_value = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.row_self_norm = nn.LayerNorm(spec.hidden_dim)
        self.row_query = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.node_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
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
        self.node_pointer_key = nn.Linear(spec.hidden_dim, spec.hidden_dim)
        self.stop_node = self.create_parameter(
            shape=[1, spec.hidden_dim],
            default_initializer=nn.initializer.Normal(std=0.02),
        )
        token_input_dim = spec.text_token_dim + spec.hidden_dim
        self.start_pointer_key = nn.Linear(token_input_dim, spec.hidden_dim)
        self.end_pointer_key = nn.Linear(token_input_dim, spec.hidden_dim)
        self.evidence_value = nn.Linear(token_input_dim, spec.hidden_dim)

    @staticmethod
    def _cross_attention_values(node_hidden: paddle.Tensor) -> paddle.Tensor:
        return node_hidden

    def _row_states(self, node_hidden: paddle.Tensor) -> paddle.Tensor:
        queries = self.row_queries
        self_scores = scaled_pointer_scores(
            self.row_self_query(queries),
            self.row_self_key(queries),
            hidden_dim=self.spec.hidden_dim,
        )
        self_context = paddle.matmul(F.softmax(self_scores, axis=1), self.row_self_value(queries))
        queries = self.row_self_norm(queries + self_context)
        node_count = node_hidden.shape[0]
        if node_count == 0:
            context = paddle.zeros_like(queries)
        else:
            scores = paddle.matmul(self.row_query(queries), self.node_key(node_hidden), transpose_y=True)
            scores = scores / math.sqrt(self.spec.hidden_dim)
            attention = F.softmax(scores, axis=1)
            context = paddle.matmul(attention, self._cross_attention_values(node_hidden))
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
        field_query_states = row_hidden.unsqueeze(1) + self.field_embedding.weight.unsqueeze(0)

        node_count = int(node_hidden.shape[0])
        text_length = int(tensors.token_ids.shape[1]) if tensors.token_ids.ndim == 2 else 0
        token_valid_mask = tensors.token_ids >= BYTE_OFFSET
        if node_count and text_length:
            contextual_tokens = paddle.concat(
                [token_states, node_hidden.unsqueeze(1).expand([-1, text_length, -1])],
                axis=2,
            )
            start_pointer_keys = self.start_pointer_key(contextual_tokens)
            end_pointer_keys = self.end_pointer_key(contextual_tokens)
            evidence_values = self.evidence_value(contextual_tokens)
        else:
            start_pointer_keys = paddle.zeros([node_count, text_length, self.spec.hidden_dim], dtype=row_hidden.dtype)
            end_pointer_keys = paddle.zeros([node_count, text_length, self.spec.hidden_dim], dtype=row_hidden.dtype)
            evidence_values = paddle.zeros([node_count, text_length, self.spec.hidden_dim], dtype=row_hidden.dtype)
        node_pointer_keys = paddle.concat([self.node_pointer_key(node_hidden), self.stop_node], axis=0)

        return ParserV51DecoderOutput(
            row_existence_logits=row_existence_logits,
            field_query_states=field_query_states,
            node_pointer_keys=node_pointer_keys,
            start_pointer_keys=start_pointer_keys,
            end_pointer_keys=end_pointer_keys,
            evidence_values=evidence_values,
            token_valid_mask=token_valid_mask,
        )


def field_node_pointer_logits(output: ParserV51DecoderOutput, state: paddle.Tensor) -> paddle.Tensor:
    hidden_dim = int(output.node_pointer_keys.shape[-1])
    return scaled_pointer_scores(state.reshape([1, hidden_dim]), output.node_pointer_keys, hidden_dim=hidden_dim)[0]


def field_span_pointer_logits(
    output: ParserV51DecoderOutput,
    state: paddle.Tensor,
    node_index: int,
) -> tuple[paddle.Tensor, paddle.Tensor]:
    node_count = int(output.start_pointer_keys.shape[0])
    if not 0 <= node_index < node_count:
        raise ValueError("Parser v5.1 span pointer node index is outside decoder memory")
    hidden_dim = int(output.start_pointer_keys.shape[-1])
    start = scaled_pointer_scores(
        state.reshape([1, hidden_dim]),
        output.start_pointer_keys[node_index],
        hidden_dim=hidden_dim,
    )[0]
    end = scaled_pointer_scores(
        state.reshape([1, hidden_dim]),
        output.end_pointer_keys[node_index],
        hidden_dim=hidden_dim,
    )[0]
    invalid = (~output.token_valid_mask[node_index]).astype(start.dtype)
    return start - invalid * 1e4, end - invalid * 1e4


def advance_field_evidence_state(
    output: ParserV51DecoderOutput,
    base_state: paddle.Tensor,
    evidence: Sequence[tuple[int, int, int]],
) -> paddle.Tensor:
    if not evidence:
        return base_state
    values: list[paddle.Tensor] = []
    for node_index, start_token, end_token in evidence:
        if end_token < start_token:
            raise ValueError("Parser v5.1 evidence span end precedes start")
        values.append(output.evidence_values[node_index, start_token : end_token + 1].mean(axis=0))
    return base_state + paddle.stack(values, axis=0).mean(axis=0)


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
    node_count = len(nodes)
    stop_index = node_count
    structural_step_limit = max(1, int(output.token_valid_mask.astype("int64").sum().item()))
    rows: list[dict[str, Any]] = []
    for row_index, score in enumerate(existence.tolist()):
        if float(score) < config.row_threshold:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            base_state = output.field_query_states[row_index, field_index]
            history: list[tuple[int, int, int]] = []
            evidence: list[dict[str, Any]] = []
            for _ in range(structural_step_limit):
                state = advance_field_evidence_state(output, base_state, history)
                node_logits = field_node_pointer_logits(output, state)
                node_index = int(node_logits.argmax().item())
                if node_index == stop_index:
                    break
                start_logits, end_logits = field_span_pointer_logits(output, state, node_index)
                start_token = int(start_logits.argmax().item())
                end_token = int(end_logits.argmax().item())
                text = _selected_text(str(nodes[node_index]["text"]), start_token, end_token)
                if text is None:
                    raise ValueError("Parser v5.1 decoded an invalid UTF-8 evidence span")
                history.append((node_index, start_token, end_token))
                evidence.append(
                    {
                        "text": text,
                        "node_id": str(nodes[node_index]["node_id"]),
                        "start_token": start_token,
                        "end_token": end_token,
                    }
                )
            else:
                raise ValueError("Parser v5.1 evidence decoder failed to emit STOP within structural bound")
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
    "advance_field_evidence_state",
    "decode_parser_v51_rows",
    "field_node_pointer_logits",
    "field_span_pointer_logits",
    "scaled_pointer_scores",
]
