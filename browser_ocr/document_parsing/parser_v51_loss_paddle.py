from __future__ import annotations

from functools import lru_cache

import paddle
import paddle.nn.functional as F

from .parser_v51_direct_decoder_paddle import (
    ParserV51DecoderOutput,
    advance_field_evidence_state,
    field_node_pointer_logits,
    field_span_pointer_logits,
)
from .parser_v51_targets import ParserV51MedicationRowTarget, ParserV51RowTargets, ROW_FIELD_ROLES, required_field_pieces


def _pointer_cross_entropy(classes: paddle.Tensor, target: int) -> paddle.Tensor:
    contiguous = classes.clone()
    return F.cross_entropy(
        contiguous.unsqueeze(0),
        paddle.to_tensor([target], dtype="int64"),
    )


def field_evidence_sequence_loss(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
    target: ParserV51MedicationRowTarget,
) -> paddle.Tensor:
    pieces = required_field_pieces(target.field(ROW_FIELD_ROLES[field_index]))
    node_count = int(output.start_pointer_keys.shape[0])
    text_length = int(output.start_pointer_keys.shape[1]) if node_count else 0
    stop_index = node_count
    base_state = output.field_query_states[row_index, field_index]
    history: list[tuple[int, int, int]] = []
    losses: list[paddle.Tensor] = []

    for piece in pieces:
        if not 0 <= piece.node_index < node_count:
            raise ValueError("Parser v5.1 evidence node index is outside decoder memory")
        start_token = piece.start_byte + 1
        end_token = piece.end_byte
        if not (1 <= start_token < text_length and start_token <= end_token < text_length):
            raise ValueError("Parser v5.1 evidence byte span is outside decoder text memory")
        state = advance_field_evidence_state(output, base_state, history)
        losses.append(_pointer_cross_entropy(field_node_pointer_logits(output, state), piece.node_index))
        start_logits, end_logits = field_span_pointer_logits(output, state, piece.node_index)
        losses.append(_pointer_cross_entropy(start_logits, start_token))
        losses.append(_pointer_cross_entropy(end_logits, end_token))
        history.append((piece.node_index, start_token, end_token))

    state = advance_field_evidence_state(output, base_state, history)
    losses.append(_pointer_cross_entropy(field_node_pointer_logits(output, state), stop_index))
    return sum(losses) / len(losses)


def field_evidence_stop_loss(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
) -> paddle.Tensor:
    """Train an unused row/field query to abstain immediately."""

    node_count = int(output.start_pointer_keys.shape[0])
    state = output.field_query_states[row_index, field_index]
    return _pointer_cross_entropy(field_node_pointer_logits(output, state), node_count)


def _row_target_cost(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    target: ParserV51MedicationRowTarget,
) -> float:
    positive_existence = F.binary_cross_entropy_with_logits(
        output.row_existence_logits[row_index],
        paddle.ones([], dtype=output.row_existence_logits.dtype),
    )
    # Product evidence is the identity of a medication row. Auxiliary fields
    # are trained after assignment, but must not decide which row slot owns a
    # truth medication when the product pointer disagrees.
    product_index = ROW_FIELD_ROLES.index("product")
    product_identity = field_evidence_sequence_loss(
        output,
        row_index=row_index,
        field_index=product_index,
        target=target,
    )
    return float(((positive_existence + product_identity) / 2.0).detach().item())


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
    costs = [
        [_row_target_cost(output, row_index=row_index, target=target) for target in targets.rows]
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
    existence_targets = paddle.zeros([row_count], dtype=output.row_existence_logits.dtype)
    for row_index, _ in assignments:
        existence_targets[row_index] = 1.0
    existence_loss = F.binary_cross_entropy_with_logits(
        output.row_existence_logits,
        existence_targets,
        reduction="mean",
    )

    field_losses: list[paddle.Tensor] = []
    matched_rows = {row_index for row_index, _ in assignments}
    for row_index, target_index in assignments:
        target = targets.rows[target_index]
        for field_index in range(len(ROW_FIELD_ROLES)):
            field_losses.append(
                field_evidence_sequence_loss(
                    output,
                    row_index=row_index,
                    field_index=field_index,
                    target=target,
                )
            )
    for row_index in range(row_count):
        if row_index in matched_rows:
            continue
        for field_index in range(len(ROW_FIELD_ROLES)):
            field_losses.append(
                field_evidence_stop_loss(
                    output,
                    row_index=row_index,
                    field_index=field_index,
                )
            )
    if not field_losses:
        return existence_loss
    return existence_loss + sum(field_losses) / len(field_losses)


__all__ = [
    "field_evidence_sequence_loss",
    "field_evidence_stop_loss",
    "match_parser_v51_rows",
    "parser_v51_set_loss",
]
