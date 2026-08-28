from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from .parser_v51_targets import ROW_FIELD_ROLES


@dataclass(frozen=True)
class ParserV51RuntimeMemory:
    """Backend-neutral neural memory consumed by the host row decoder."""

    row_existence_logits: np.ndarray
    field_query_states: np.ndarray
    node_pointer_keys: np.ndarray
    start_pointer_keys: np.ndarray
    end_pointer_keys: np.ndarray
    evidence_values: np.ndarray
    token_valid_mask: np.ndarray


@dataclass(frozen=True)
class ParserV51RuntimeDecodeConfig:
    row_threshold: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.row_threshold) <= 1.0:
            raise ValueError("Parser v5.1 row_threshold must be in [0, 1]")


def _validated_memory(
    memory: ParserV51RuntimeMemory,
    *,
    node_count: int,
) -> tuple[int, int, int, int]:
    row_logits = np.asarray(memory.row_existence_logits)
    field_states = np.asarray(memory.field_query_states)
    node_keys = np.asarray(memory.node_pointer_keys)
    start_keys = np.asarray(memory.start_pointer_keys)
    end_keys = np.asarray(memory.end_pointer_keys)
    evidence_values = np.asarray(memory.evidence_values)
    valid = np.asarray(memory.token_valid_mask)
    if row_logits.ndim != 1:
        raise ValueError("Parser v5.1 runtime row logits must be rank 1")
    if field_states.ndim != 3 or field_states.shape[0] != row_logits.shape[0]:
        raise ValueError("Parser v5.1 runtime field query shape is invalid")
    if field_states.shape[1] != len(ROW_FIELD_ROLES):
        raise ValueError("Parser v5.1 runtime field query role count is invalid")
    hidden_dim = int(field_states.shape[2])
    if hidden_dim <= 0 or node_keys.shape != (node_count + 1, hidden_dim):
        raise ValueError("Parser v5.1 runtime node pointer memory shape is invalid")
    if start_keys.ndim != 3 or start_keys.shape[0] != node_count or start_keys.shape[2] != hidden_dim:
        raise ValueError("Parser v5.1 runtime start pointer memory shape is invalid")
    if end_keys.shape != start_keys.shape or evidence_values.shape != start_keys.shape:
        raise ValueError("Parser v5.1 runtime span/evidence memory shapes disagree")
    text_length = int(start_keys.shape[1])
    if valid.shape != (node_count, text_length):
        raise ValueError("Parser v5.1 runtime token-valid mask shape is invalid")
    arrays = (row_logits, field_states, node_keys, start_keys, end_keys, evidence_values)
    if any(not np.isfinite(value).all() for value in arrays):
        raise ValueError("Parser v5.1 runtime memory contains non-finite values")
    return int(row_logits.shape[0]), len(ROW_FIELD_ROLES), text_length, hidden_dim


def _scaled_scores(state: np.ndarray, keys: np.ndarray, *, hidden_dim: int) -> np.ndarray:
    return np.matmul(keys, state) / math.sqrt(hidden_dim)


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponent = math.exp(-value)
        return 1.0 / (1.0 + exponent)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _advance_state(
    memory: ParserV51RuntimeMemory,
    base_state: np.ndarray,
    evidence: Sequence[tuple[int, int, int]],
) -> np.ndarray:
    if not evidence:
        return np.asarray(base_state)
    values = []
    for node_index, start_token, end_token in evidence:
        if end_token < start_token:
            raise ValueError("Parser v5.1 runtime evidence span end precedes start")
        values.append(np.asarray(memory.evidence_values[node_index, start_token : end_token + 1]).mean(axis=0))
    return np.asarray(base_state) + np.stack(values, axis=0).mean(axis=0)


def _character_boundaries(
    text: str,
    *,
    token_valid: np.ndarray,
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """Return legal start/end token positions paired with character indices."""

    byte_offset = 0
    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    token_count = int(token_valid.shape[0])
    for char_index, char in enumerate(text):
        byte_count = len(char.encode("utf-8"))
        start_token = byte_offset + 1
        end_token = byte_offset + byte_count
        byte_offset += byte_count
        if end_token >= token_count:
            break
        if bool(token_valid[start_token]) and bool(token_valid[end_token]):
            starts.append((start_token, char_index))
            ends.append((end_token, char_index + 1))
    return tuple(starts), tuple(ends)


def _decode_span(
    *,
    text: str,
    state: np.ndarray,
    start_keys: np.ndarray,
    end_keys: np.ndarray,
    token_valid: np.ndarray,
    hidden_dim: int,
    minimum_start_char: int = 0,
) -> tuple[int, int, int, int, str]:
    starts, ends = _character_boundaries(text, token_valid=token_valid)
    starts = tuple(item for item in starts if item[1] >= minimum_start_char)
    if not starts or not ends:
        raise ValueError("Parser v5.1 runtime selected a node with no decodable text")
    start_scores = _scaled_scores(state, start_keys, hidden_dim=hidden_dim)
    end_scores = _scaled_scores(state, end_keys, hidden_dim=hidden_dim)
    candidates = [
        (start_token, end_token, start_char, end_char)
        for start_token, start_char in starts
        for end_token, end_char in ends
        if end_char > start_char and end_token >= start_token
    ]
    if not candidates:
        raise ValueError("Parser v5.1 runtime could not decode a valid field span")
    start_token, end_token, start_char, end_char = max(
        candidates,
        key=lambda item: float(start_scores[item[0]] + end_scores[item[1]]),
    )
    selected = text[start_char:end_char]
    if not selected.strip():
        raise ValueError("Parser v5.1 runtime decoded whitespace-only evidence")
    return start_token, end_token, start_char, end_char, selected


def _node_has_forward_span(
    *,
    text: str,
    token_valid: np.ndarray,
    minimum_start_char: int,
) -> bool:
    starts, ends = _character_boundaries(text, token_valid=token_valid)
    return any(
        end_char > start_char
        for _, start_char in starts
        if start_char >= minimum_start_char
        for _, end_char in ends
    )


def decode_parser_v51_memory(
    *,
    nodes: Sequence[Mapping[str, Any]],
    memory: ParserV51RuntimeMemory,
    config: ParserV51RuntimeDecodeConfig = ParserV51RuntimeDecodeConfig(),
) -> list[dict[str, Any]]:
    """Decode medication rows using only OCR text and backend-neutral memories.

    The decoder is extractive: every emitted value is reconstructed from one
    or more literal OCR substrings. OCR nodes that are never selected by any
    field simply disappear from the output; no header/context taxonomy is
    required at runtime.
    """

    node_count = len(nodes)
    row_count, _, _, hidden_dim = _validated_memory(memory, node_count=node_count)
    stop_index = node_count
    # At most one new non-overlapping evidence fragment can begin at each
    # valid payload position, followed by one final STOP decision.
    structural_step_limit = max(1, int(np.asarray(memory.token_valid_mask, dtype=np.int64).sum()) + 1)
    rows: list[dict[str, Any]] = []
    for row_index in range(row_count):
        score = _sigmoid(float(memory.row_existence_logits[row_index]))
        if score < config.row_threshold:
            continue
        fields: dict[str, dict[str, Any]] = {}
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            base_state = np.asarray(memory.field_query_states[row_index, field_index])
            history: list[tuple[int, int, int]] = []
            evidence: list[dict[str, Any]] = []
            last_position: tuple[int, int] | None = None
            for _ in range(structural_step_limit):
                state = _advance_state(memory, base_state, history)
                node_scores = _scaled_scores(state, np.asarray(memory.node_pointer_keys), hidden_dim=hidden_dim)
                if last_position is not None:
                    last_node, last_end_char = last_position
                    for candidate_node in range(node_count):
                        minimum_start = last_end_char if candidate_node == last_node else 0
                        if candidate_node < last_node or not _node_has_forward_span(
                            text=str(nodes[candidate_node]["text"]),
                            token_valid=np.asarray(memory.token_valid_mask[candidate_node], dtype=bool),
                            minimum_start_char=minimum_start,
                        ):
                            node_scores[candidate_node] = -np.inf
                node_index = int(np.argmax(node_scores))
                if node_index == stop_index:
                    break
                minimum_start_char = 0
                if last_position is not None and node_index == last_position[0]:
                    minimum_start_char = last_position[1]
                start_token, end_token, start_char, end_char, text = _decode_span(
                    text=str(nodes[node_index]["text"]),
                    state=state,
                    start_keys=np.asarray(memory.start_pointer_keys[node_index]),
                    end_keys=np.asarray(memory.end_pointer_keys[node_index]),
                    token_valid=np.asarray(memory.token_valid_mask[node_index], dtype=bool),
                    hidden_dim=hidden_dim,
                    minimum_start_char=minimum_start_char,
                )
                history.append((node_index, start_token, end_token))
                last_position = (node_index, end_char)
                evidence.append(
                    {
                        "text": text,
                        "node_id": str(nodes[node_index]["node_id"]),
                        "start_char": start_char,
                        "end_char": end_char,
                        "start_token": start_token,
                        "end_token": end_token,
                    }
                )
            else:
                raise ValueError("Parser v5.1 runtime evidence decoder failed to emit STOP within structural bound")
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
                "row_confidence": score,
                "product_query": product["text"],
                "product_evidence": product["evidence"],
                "fields": fields,
            }
        )
    return rows


__all__ = [
    "ParserV51RuntimeDecodeConfig",
    "ParserV51RuntimeMemory",
    "decode_parser_v51_memory",
]