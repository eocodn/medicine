from __future__ import annotations

import math
from typing import Mapping, Sequence

from .learned_features import LayoutNode, LabeledDocument, NODE_FEATURE_DIM, bounds, center, node_features


NEIGHBOR_COUNT = 6
_DIRECTION_COUNT = 6
_NEIGHBOR_FEATURE_DIM = 15


def _overlap_ratio(a1: float, a2: float, b1: float, b2: float) -> float:
    intersection = max(0.0, min(a2, b2) - max(a1, b1))
    return intersection / max(min(a2 - a1, b2 - b1), 1.0)


def contextual_node_features(
    document: LabeledDocument,
    node: LayoutNode,
    local_scores: Mapping[str, Mapping[str, float]],
    role_order: Sequence[str],
) -> list[float]:
    if node.box_id not in local_scores:
        raise ValueError(f"missing local role scores for {node.box_id}")
    role_count = len(role_order)
    self_scores = [float(local_scores[node.box_id][role]) for role in role_order]
    global_scores = [0.0] * role_count
    for candidate in document.nodes:
        candidate_scores = local_scores.get(candidate.box_id)
        if candidate_scores is None:
            raise ValueError(f"missing local role scores for {candidate.box_id}")
        for index, role in enumerate(role_order):
            global_scores[index] += float(candidate_scores[role])
    divisor = max(len(document.nodes), 1)
    global_scores = [value / divisor for value in global_scores]

    cx, cy, width, height = center(node)
    x1, y1, x2, y2 = bounds(node)
    neighbors: list[tuple[float, str, LayoutNode, list[float], float, float, float, float]] = []
    for candidate in document.nodes:
        if candidate.box_id == node.box_id:
            continue
        ncx, ncy, nwidth, nheight = center(candidate)
        nx1, ny1, nx2, ny2 = bounds(candidate)
        dx = (ncx - cx) / document.width
        dy = (ncy - cy) / document.height
        distance = math.hypot(dx, dy)
        vertical_overlap = _overlap_ratio(y1, y2, ny1, ny2)
        horizontal_overlap = _overlap_ratio(x1, x2, nx1, nx2)
        probabilities = [float(local_scores[candidate.box_id][role]) for role in role_order]
        neighbors.append(
            (
                distance,
                candidate.box_id,
                candidate,
                probabilities,
                dx,
                dy,
                vertical_overlap,
                horizontal_overlap,
            )
        )
    neighbors.sort(key=lambda item: (item[0], item[1]))

    # Aggregates let the second-stage classifier use more than the K nearest boxes.
    # The bins are purely geometric and never inspect GT labels.
    direction_scores = [[0.0] * role_count for _ in range(_DIRECTION_COUNT)]
    direction_weights = [0.0] * _DIRECTION_COUNT
    for distance, _, _, probabilities, dx, dy, vertical_overlap, horizontal_overlap in neighbors:
        weight = 1.0 / (0.05 + distance)
        bins = []
        if dx < 0:
            bins.append(0)  # left
        if dx >= 0:
            bins.append(1)  # right
        if dy < 0:
            bins.append(2)  # above
        if dy >= 0:
            bins.append(3)  # below
        if vertical_overlap >= 0.5:
            bins.append(4)  # same visual row
        if horizontal_overlap >= 0.5:
            bins.append(5)  # same visual column
        for bin_index in bins:
            direction_weights[bin_index] += weight
            for role_index, probability in enumerate(probabilities):
                direction_scores[bin_index][role_index] += weight * probability
    direction_features: list[float] = []
    for scores, total_weight in zip(direction_scores, direction_weights, strict=True):
        if total_weight:
            direction_features.extend(value / total_weight for value in scores)
        else:
            direction_features.extend([0.0] * role_count)

    nearest_features: list[float] = []
    for neighbor_index in range(NEIGHBOR_COUNT):
        if neighbor_index >= len(neighbors):
            nearest_features.extend([0.0] * _NEIGHBOR_FEATURE_DIM)
            continue
        distance, _, candidate, probabilities, dx, dy, vertical_overlap, horizontal_overlap = neighbors[neighbor_index]
        _, _, neighbor_width, neighbor_height = center(candidate)
        nearest_features.extend(
            [
                dx,
                dy,
                abs(dx),
                abs(dy),
                distance,
                vertical_overlap,
                horizontal_overlap,
                1.0 if vertical_overlap >= 0.5 else 0.0,
                1.0 if horizontal_overlap >= 0.5 else 0.0,
                max(0.0, min(candidate.confidence, 1.0)),
                *probabilities,
            ]
        )
        if len(nearest_features) % _NEIGHBOR_FEATURE_DIM != 0:
            raise AssertionError("context neighbor feature dimension changed unexpectedly")

    features = [
        *node_features(node, width=document.width, height=document.height),
        *self_scores,
        *global_scores,
        *direction_features,
        *nearest_features,
    ]
    expected = NODE_FEATURE_DIM + role_count * (2 + _DIRECTION_COUNT) + NEIGHBOR_COUNT * _NEIGHBOR_FEATURE_DIM
    if len(features) != expected:
        raise AssertionError("context feature dimension changed unexpectedly")
    return [float(value) for value in features]


def context_feature_dim(role_count: int) -> int:
    return NODE_FEATURE_DIM + role_count * (2 + _DIRECTION_COUNT) + NEIGHBOR_COUNT * _NEIGHBOR_FEATURE_DIM


__all__ = ["NEIGHBOR_COUNT", "context_feature_dim", "contextual_node_features"]