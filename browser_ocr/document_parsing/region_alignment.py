from __future__ import annotations

from typing import Any, Mapping, Sequence


def polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        total += float(point[0]) * float(next_point[1]) - float(next_point[0]) * float(point[1])
    return abs(total) / 2.0


def _signed_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        total += float(point[0]) * float(next_point[1]) - float(next_point[0]) * float(point[1])
    return total / 2.0


def _line_intersection(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
) -> list[float]:
    denominator = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(denominator) < 1e-9:
        return [float(b[0]), float(b[1])]
    determinant_a = a[0] * b[1] - a[1] * b[0]
    determinant_b = c[0] * d[1] - c[1] * d[0]
    return [
        (determinant_a * (c[0] - d[0]) - (a[0] - b[0]) * determinant_b) / denominator,
        (determinant_a * (c[1] - d[1]) - (a[1] - b[1]) * determinant_b) / denominator,
    ]


def _clip_convex(subject: Sequence[Sequence[float]], clip: Sequence[Sequence[float]]) -> list[list[float]]:
    output = [[float(point[0]), float(point[1])] for point in subject]
    clockwise = _signed_area(clip) < 0

    def inside(point: Sequence[float], edge_start: Sequence[float], edge_end: Sequence[float]) -> bool:
        cross = (edge_end[0] - edge_start[0]) * (point[1] - edge_start[1]) - (
            edge_end[1] - edge_start[1]
        ) * (point[0] - edge_start[0])
        return cross <= 1e-9 if clockwise else cross >= -1e-9

    for edge_index, edge_start in enumerate(clip):
        edge_end = clip[(edge_index + 1) % len(clip)]
        input_points = output
        output = []
        if not input_points:
            break
        previous = input_points[-1]
        for current in input_points:
            current_inside = inside(current, edge_start, edge_end)
            previous_inside = inside(previous, edge_start, edge_end)
            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, current, edge_start, edge_end))
                output.append([float(current[0]), float(current[1])])
            elif previous_inside:
                output.append(_line_intersection(previous, current, edge_start, edge_end))
            previous = current
    return output


def region_overlap(
    truth_polygon: Sequence[Sequence[float]],
    observed_polygon: Sequence[Sequence[float]],
) -> tuple[float, float, float]:
    truth_area = polygon_area(truth_polygon)
    observed_area = polygon_area(observed_polygon)
    intersection_polygon = _clip_convex(truth_polygon, observed_polygon)
    intersection = polygon_area(intersection_polygon) if len(intersection_polygon) >= 3 else 0.0
    union = truth_area + observed_area - intersection
    truth_coverage = intersection / truth_area if truth_area > 0 else 0.0
    observed_coverage = intersection / observed_area if observed_area > 0 else 0.0
    iou = intersection / union if union > 0 else 0.0
    return truth_coverage, observed_coverage, iou


def core_polygon(region: Mapping[str, Any]) -> Sequence[Sequence[float]]:
    return region.get("natural_text_polygon") or region["polygon"]


def observed_region_id(region: Mapping[str, Any], fallback_index: int) -> str:
    raw_index = region.get("index", fallback_index)
    if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index <= 0:
        raise ValueError("observed region index must be a positive integer")
    return f"region-{raw_index:04d}"


def match_region_candidates(
    truth_regions: Sequence[Mapping[str, Any]],
    observed_region: Mapping[str, Any],
    *,
    minimum_truth_coverage: float = 0.45,
    minimum_observed_coverage: float = 0.80,
) -> list[tuple[Mapping[str, Any], tuple[float, float, float]]]:
    matches: list[tuple[Mapping[str, Any], tuple[float, float, float]]] = []
    for truth in truth_regions:
        overlap = region_overlap(core_polygon(truth), observed_region["polygon"])
        truth_coverage, observed_coverage, _ = overlap
        if truth_coverage >= minimum_truth_coverage or observed_coverage >= minimum_observed_coverage:
            matches.append((truth, overlap))
    matches.sort(
        key=lambda item: (
            item[1][0],
            item[1][1],
            item[1][2],
            str(item[0].get("region_id") or ""),
        ),
        reverse=True,
    )
    return matches


def match_regions_one_to_one(
    truth_regions: Sequence[Mapping[str, Any]],
    observed_regions: Sequence[Mapping[str, Any]],
    *,
    minimum_truth_coverage: float = 0.80,
) -> dict[str, Mapping[str, Any]]:
    candidates: list[tuple[float, float, float, int, int]] = []
    for truth_index, truth in enumerate(truth_regions):
        for observed_index, observed in enumerate(observed_regions):
            truth_coverage, observed_coverage, iou = region_overlap(core_polygon(truth), observed["polygon"])
            if truth_coverage >= minimum_truth_coverage:
                candidates.append((truth_coverage, observed_coverage, iou, truth_index, observed_index))
    candidates.sort(reverse=True)
    truth_used: set[int] = set()
    observed_used: set[int] = set()
    mapping: dict[str, Mapping[str, Any]] = {}
    for _, _, _, truth_index, observed_index in candidates:
        if truth_index in truth_used or observed_index in observed_used:
            continue
        truth_used.add(truth_index)
        observed_used.add(observed_index)
        observed = observed_regions[observed_index]
        mapping[observed_region_id(observed, observed_index + 1)] = truth_regions[truth_index]
    return mapping


__all__ = [
    "core_polygon",
    "match_region_candidates",
    "match_regions_one_to_one",
    "observed_region_id",
    "polygon_area",
    "region_overlap",
]
