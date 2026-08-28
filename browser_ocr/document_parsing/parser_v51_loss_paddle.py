from __future__ import annotations

from functools import lru_cache

import paddle
import paddle.nn.functional as F

from .parser_v51_direct_decoder_paddle import ParserV51DecoderOutput
from .parser_v51_targets import (
    ParserV51FieldTarget,
    ParserV51MedicationRowTarget,
    ParserV51RowTargets,
    ROW_FIELD_ROLES,
    required_field_pieces,
)


def _membership_set_loss(logits: paddle.Tensor, targets: paddle.Tensor) -> paddle.Tensor:
    """Calibrated sparse membership plus direct set-overlap supervision.

    Ordinary BCE preserves the true sparse node prior, while soft Dice keeps
    the few positive fragments from being washed out by many irrelevant OCR
    nodes. Class-reweighted BCE is deliberately avoided because it changes the
    implied posterior prior and made a fixed 0.5 runtime decision over-select
    nodes in the first v5.1 sanity run.
    """

    bce = F.binary_cross_entropy_with_logits(logits, targets, reduction="mean")
    if not bool((targets > 0.5).any().item()):
        return bce
    probabilities = F.sigmoid(logits)
    intersection = (probabilities * targets).sum()
    denominator = probabilities.sum() + targets.sum()
    smooth = paddle.to_tensor(1e-6, dtype=logits.dtype)
    dice = 1.0 - (2.0 * intersection + smooth) / (denominator + smooth)
    return (bce + dice) / 2.0


def _field_loss(
    output: ParserV51DecoderOutput,
    *,
    row_index: int,
    field_index: int,
    field: ParserV51FieldTarget,
    node_count: int,
    text_length: int,
) -> paddle.Tensor:
    present = bool(field.pieces)
    presence_target = paddle.to_tensor(1.0 if present else 0.0, dtype=output.field_presence_logits.dtype)
    presence_loss = F.binary_cross_entropy_with_logits(
        output.field_presence_logits[row_index, field_index],
        presence_target,
    )
    if node_count == 0:
        return presence_loss

    required = required_field_pieces(field)
    membership_target = paddle.zeros([node_count], dtype=output.field_node_logits.dtype)
    for piece in required:
        if 0 <= piece.node_index < node_count:
            membership_target[piece.node_index] = 1.0
    membership_loss = _membership_set_loss(
        output.field_node_logits[row_index, field_index],
        membership_target,
    )
    if not required or text_length == 0:
        return (presence_loss + membership_loss) / 2.0

    # At most one contiguous source span is decoded per selected OCR node. If
    # training provenance presents multiple alternatives in one node, use the
    # first canonical piece; multi-node split fragments remain independently
    # supervised and are concatenated by the runtime decoder.
    piece_by_node = {}
    for piece in required:
        piece_by_node.setdefault(piece.node_index, piece)
    span_losses: list[paddle.Tensor] = []
    for node_index, piece in sorted(piece_by_node.items()):
        if not 0 <= node_index < node_count:
            continue
        start_token = piece.start_byte + 1
        end_token = piece.end_byte
        if not (1 <= start_token < text_length and start_token <= end_token < text_length):
            continue
        start_target = paddle.to_tensor([start_token], dtype="int64")
        end_target = paddle.to_tensor([end_token], dtype="int64")
        span_losses.append(
            F.cross_entropy(
                output.field_start_logits[row_index, field_index, node_index].reshape([1, text_length]),
                start_target,
            )
        )
        span_losses.append(
            F.cross_entropy(
                output.field_end_logits[row_index, field_index, node_index].reshape([1, text_length]),
                end_target,
            )
        )
    components = [presence_loss, membership_loss]
    if span_losses:
        components.append(sum(span_losses) / len(span_losses))
    return sum(components) / len(components)


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
            _field_loss(
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
    node_count = int(output.field_node_logits.shape[2])
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
    node_count = int(output.field_node_logits.shape[2])
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
                _field_loss(
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
