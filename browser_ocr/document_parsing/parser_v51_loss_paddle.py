from __future__ import annotations

from functools import lru_cache

import paddle
import paddle.nn.functional as F

from .parser_v51_direct_decoder_paddle import ParserV51DecoderOutput
from .parser_v51_targets import ParserV51MedicationRowTarget, ParserV51RowTargets, ROW_FIELD_ROLES, required_field_pieces


def field_token_targets(
    output: ParserV51DecoderOutput,
    target: ParserV51MedicationRowTarget,
    field_index: int,
) -> paddle.Tensor:
    node_count = int(output.field_token_logits.shape[2])
    text_length = int(output.field_token_logits.shape[3])
    selected = paddle.zeros([node_count, text_length], dtype="bool")
    field = target.field(ROW_FIELD_ROLES[field_index])
    for piece in required_field_pieces(field):
        if not 0 <= piece.node_index < node_count:
            raise ValueError("Parser v5.1 field segment node index is outside decoder input")
        start = max(1, piece.start_byte + 1)
        stop = min(text_length, piece.end_byte + 1)
        if stop > start:
            selected[piece.node_index, start:stop] = True
    return selected & output.token_valid_mask


def _field_token_loss(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
    target: ParserV51MedicationRowTarget,
) -> paddle.Tensor:
    valid = output.token_valid_mask
    if not bool(valid.any().item()):
        return output.row_existence_logits[row_index] * 0.0
    selected = field_token_targets(output, target, field_index)
    logits = output.field_token_logits[row_index, field_index][valid].clone()
    labels = selected[valid].astype("int64")
    losses = F.cross_entropy(logits, labels, reduction="none")
    positives = labels == 1
    negatives = ~positives
    components: list[paddle.Tensor] = []
    if bool(positives.any().item()):
        components.append(losses[positives].mean())
    if bool(negatives.any().item()):
        components.append(losses[negatives].mean())
    return sum(components) / len(components)


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
    components = [positive_existence]
    for field_index in range(len(ROW_FIELD_ROLES)):
        components.append(
            _field_token_loss(
                output,
                row_index=row_index,
                field_index=field_index,
                target=target,
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
    for row_index, target_index in assignments:
        target = targets.rows[target_index]
        for field_index in range(len(ROW_FIELD_ROLES)):
            field_losses.append(
                _field_token_loss(
                    output,
                    row_index=row_index,
                    field_index=field_index,
                    target=target,
                )
            )
    if not field_losses:
        return existence_loss
    return existence_loss + sum(field_losses) / len(field_losses)


__all__ = ["field_token_targets", "match_parser_v51_rows", "parser_v51_set_loss"]
