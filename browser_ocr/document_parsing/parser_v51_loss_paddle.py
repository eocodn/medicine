from __future__ import annotations

from functools import lru_cache

import paddle
import paddle.nn.functional as F

from .parser_v51_direct_decoder_paddle import ParserV51DecoderOutput, pointer_class_logits
from .parser_v51_targets import ParserV51MedicationRowTarget, ParserV51RowTargets, ROW_FIELD_ROLES, required_field_pieces


def _pointer_cross_entropy(classes: paddle.Tensor, target: int) -> paddle.Tensor:
    """Cross entropy for one logical pointer vector without reordering a slice."""

    # `pointer_class_logits` is assembled by flatten+concat and then indexed by
    # row/field/piece. Paddle can preserve a non-contiguous view here whose
    # values index correctly but whose fused cross-entropy kernel observes a
    # different physical stride. Materialize the logical class order before
    # handing it to CE; clone remains differentiable and preserves gradients.
    contiguous = classes.clone()
    return F.cross_entropy(
        contiguous.unsqueeze(0),
        paddle.to_tensor([target], dtype="int64"),
    )


def _field_pointer_loss(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
    target: ParserV51MedicationRowTarget,
) -> paddle.Tensor:
    field = target.field(ROW_FIELD_ROLES[field_index])
    required = required_field_pieces(field)
    max_pieces = int(output.piece_start_logits.shape[2])
    node_count = int(output.piece_start_logits.shape[3])
    text_length = int(output.piece_start_logits.shape[4])
    if len(required) > max_pieces:
        raise ValueError("Parser v5.1 field fragments exceed decoder piece slots")
    none_index = node_count * text_length
    start_classes = pointer_class_logits(output.piece_start_logits, output.piece_none_logits[..., 0])
    end_classes = pointer_class_logits(output.piece_end_logits, output.piece_none_logits[..., 1])
    losses: list[paddle.Tensor] = []
    for piece_index in range(max_pieces):
        if piece_index < len(required):
            piece = required[piece_index]
            start_token = piece.start_byte + 1
            end_token = piece.end_byte
            if not (0 <= piece.node_index < node_count):
                raise ValueError("Parser v5.1 piece node index is outside decoder input")
            if not (1 <= start_token < text_length and start_token <= end_token < text_length):
                raise ValueError("Parser v5.1 piece byte span is outside decoder text input")
            start_target = piece.node_index * text_length + start_token
            end_target = piece.node_index * text_length + end_token
        else:
            start_target = none_index
            end_target = none_index
        losses.append(
            _pointer_cross_entropy(start_classes[row_index, field_index, piece_index], start_target)
        )
        losses.append(
            _pointer_cross_entropy(end_classes[row_index, field_index, piece_index], end_target)
        )
    return sum(losses) / len(losses)


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
            _field_pointer_loss(
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
                _field_pointer_loss(
                    output,
                    row_index=row_index,
                    field_index=field_index,
                    target=target,
                )
            )
    if not field_losses:
        return existence_loss
    return existence_loss + sum(field_losses) / len(field_losses)


__all__ = ["match_parser_v51_rows", "parser_v51_set_loss"]
