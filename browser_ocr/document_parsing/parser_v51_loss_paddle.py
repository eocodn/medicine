from __future__ import annotations

from functools import lru_cache

import paddle
import paddle.nn.functional as F

from .parser_v51_direct_decoder_paddle import ParserV51DecoderOutput
from .parser_v51_targets import ParserV51FieldTarget, ParserV51MedicationRowTarget, ParserV51RowTargets, ROW_FIELD_ROLES


def _valid_piece_keys(
    field: ParserV51FieldTarget,
    *,
    node_count: int,
    text_length: int,
) -> tuple[tuple[int, int, int], ...]:
    values: set[tuple[int, int, int]] = set()
    for piece in field.pieces:
        start_token = piece.start_byte + 1
        end_token = piece.end_byte
        if (
            0 <= piece.node_index < node_count
            and 1 <= start_token < text_length
            and start_token <= end_token < text_length
        ):
            values.add((piece.node_index, start_token, end_token))
    return tuple(sorted(values))


def _field_nll(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
    field: ParserV51FieldTarget,
    node_count: int,
    text_length: int,
) -> paddle.Tensor:
    node_log_probs = F.log_softmax(output.field_node_logits[row_index, field_index], axis=0)
    pieces = _valid_piece_keys(field, node_count=node_count, text_length=text_length)
    if not field.pieces:
        return -node_log_probs[node_count]
    if not pieces:
        # The source text exists but falls outside the encoded byte window.
        # Keep node selection trainable without inventing an impossible span.
        node_indices = sorted({piece.node_index for piece in field.pieces if 0 <= piece.node_index < node_count})
        if not node_indices:
            return paddle.zeros([], dtype=output.field_node_logits.dtype)
        return -paddle.logsumexp(paddle.stack([node_log_probs[index] for index in node_indices]), axis=0)

    start_log_probs = F.log_softmax(output.field_start_logits[row_index, field_index], axis=1)
    end_log_probs = F.log_softmax(output.field_end_logits[row_index, field_index], axis=1)
    scores = [
        node_log_probs[node_index]
        + start_log_probs[node_index, start_token]
        + end_log_probs[node_index, end_token]
        for node_index, start_token, end_token in pieces
    ]
    # Multiple observed pieces can arise from OCR duplication or fragmentation.
    # The first direct decoder is allowed to select any visible piece; a later
    # fragment-linking stage can require complete multi-node reconstruction.
    return -paddle.logsumexp(paddle.stack(scores), axis=0)


def _row_target_cost(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    target: ParserV51MedicationRowTarget,
    node_count: int,
    text_length: int,
) -> float:
    positive_existence = F.binary_cross_entropy_with_logits(
        output.row_existence_logits[row_index],
        paddle.ones([], dtype=output.row_existence_logits.dtype),
    )
    components = [positive_existence]
    for field_index, role in enumerate(ROW_FIELD_ROLES):
        components.append(
            _field_nll(
                output,
                row_index=row_index,
                field_index=field_index,
                field=target.field(role),
                node_count=node_count,
                text_length=text_length,
            )
        )
    return float((sum(components) / len(components)).detach().item())


def match_parser_v51_rows(
    output: ParserV51DecoderOutput,
    targets: ParserV51RowTargets,
) -> tuple[tuple[int, int], ...]:
    row_count = int(output.row_existence_logits.shape[0])
    target_count = len(targets.rows)
    if target_count > row_count:
        raise ValueError("Parser v5.1 target rows exceed decoder row slots")
    if target_count == 0:
        return ()
    node_count = int(output.field_node_logits.shape[2]) - 1
    text_length = int(output.field_start_logits.shape[3]) if node_count else 0
    costs = [
        [
            _row_target_cost(
                output,
                row_index=row_index,
                target=target,
                node_count=node_count,
                text_length=text_length,
            )
            for target in targets.rows
        ]
        for row_index in range(row_count)
    ]

    @lru_cache(maxsize=None)
    def solve(target_index: int, used_rows: int) -> tuple[float, tuple[int, ...]]:
        if target_index == target_count:
            return 0.0, ()
        best_cost = float("inf")
        best_rows: tuple[int, ...] = ()
        for row_index in range(row_count):
            bit = 1 << row_index
            if used_rows & bit:
                continue
            remainder_cost, remainder_rows = solve(target_index + 1, used_rows | bit)
            candidate_cost = costs[row_index][target_index] + remainder_cost
            if candidate_cost < best_cost:
                best_cost = candidate_cost
                best_rows = (row_index, *remainder_rows)
        return best_cost, best_rows

    _, matched_rows = solve(0, 0)
    return tuple((row_index, target_index) for target_index, row_index in enumerate(matched_rows))


def parser_v51_set_loss(
    output: ParserV51DecoderOutput,
    targets: ParserV51RowTargets,
) -> paddle.Tensor:
    assignments = match_parser_v51_rows(output, targets)
    row_count = int(output.row_existence_logits.shape[0])
    node_count = int(output.field_node_logits.shape[2]) - 1
    text_length = int(output.field_start_logits.shape[3]) if node_count else 0
    existence_targets = paddle.zeros([row_count], dtype=output.row_existence_logits.dtype)
    for row_index, _ in assignments:
        existence_targets[row_index] = 1.0
    existence_loss = F.binary_cross_entropy_with_logits(
        output.row_existence_logits,
        existence_targets,
        reduction="mean",
    )

    field_losses: list[paddle.Tensor] = []
    for row_index, target_index in assignments:
        target = targets.rows[target_index]
        for field_index, role in enumerate(ROW_FIELD_ROLES):
            field_losses.append(
                _field_nll(
                    output,
                    row_index=row_index,
                    field_index=field_index,
                    field=target.field(role),
                    node_count=node_count,
                    text_length=text_length,
                )
            )
    if not field_losses:
        return existence_loss
    return existence_loss + sum(field_losses) / len(field_losses)


__all__ = ["match_parser_v51_rows", "parser_v51_set_loss"]