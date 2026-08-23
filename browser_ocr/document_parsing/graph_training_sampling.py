from __future__ import annotations

import math
import random
from typing import Sequence


def normalize_view_weights(count: int, weights: Sequence[float] | None) -> tuple[float, ...]:
    if count <= 0:
        raise ValueError("training view count must be positive")
    if weights is None:
        if count != 1:
            raise ValueError("train_weights are required when multiple train manifests are supplied")
        return (1.0,)
    if len(weights) != count:
        raise ValueError("train_weights must have exactly one value per train manifest")
    normalized: list[float] = []
    for value in weights:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("train_weights must be numeric")
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            raise ValueError("train_weights must be positive and finite")
        normalized.append(number)
    total = sum(normalized)
    return tuple(value / total for value in normalized)


def _step_counts(total_steps: int, weights: Sequence[float]) -> list[int]:
    raw = [total_steps * weight for weight in weights]
    counts = [math.floor(value) for value in raw]
    remaining = total_steps - sum(counts)
    ranked = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - counts[index], -index),
        reverse=True,
    )
    for index in ranked[:remaining]:
        counts[index] += 1
    return counts


def weighted_epoch_schedule(
    view_lengths: Sequence[int],
    weights: Sequence[float],
    *,
    seed: int,
    epoch: int,
) -> tuple[list[tuple[int, int]], tuple[int, ...]]:
    if not view_lengths or len(view_lengths) != len(weights):
        raise ValueError("training view lengths and weights must be non-empty and aligned")
    if any(length <= 0 for length in view_lengths):
        raise ValueError("training views must contain at least one graph")
    total_steps = sum(view_lengths)
    counts = _step_counts(total_steps, weights)
    schedule: list[tuple[int, int]] = []
    for view_index, (length, count) in enumerate(zip(view_lengths, counts, strict=True)):
        rng = random.Random(seed ^ ((epoch + 1) * 0x9E3779B1) ^ ((view_index + 1) * 0x85EBCA6B))
        emitted = 0
        while emitted < count:
            order = list(range(length))
            rng.shuffle(order)
            take = min(count - emitted, length)
            schedule.extend((view_index, graph_index) for graph_index in order[:take])
            emitted += take
    random.Random(seed ^ ((epoch + 1) * 0xC2B2AE35)).shuffle(schedule)
    return schedule, tuple(counts)


__all__ = ["normalize_view_weights", "weighted_epoch_schedule"]