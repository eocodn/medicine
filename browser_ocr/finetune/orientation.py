from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from .dataset import DatasetError


RIGHT_ANGLES = (0, 90, 180, 270)


def _point(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DatasetError(f"{label} must contain x/y")
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"{label} coordinates must be numeric") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise DatasetError(f"{label} coordinates must be finite")
    return x, y


def _polygon(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) != 4:
        raise DatasetError("detector prediction polygon must contain four points")
    return [_point(point, "detector prediction polygon point") for point in value]


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _axes(polygon: Sequence[tuple[float, float]]) -> tuple[float, float]:
    width = max(_distance(polygon[0], polygon[1]), _distance(polygon[2], polygon[3]))
    height = max(_distance(polygon[0], polygon[3]), _distance(polygon[1], polygon[2]))
    return max(width, 1e-9), max(height, 1e-9)


def orientation_candidates(predictions: Iterable[Mapping[str, object]]) -> tuple[int, ...]:
    """Reduce right-angle orientation candidates from detector box geometry.

    Horizontal text and the same text rotated by 180 degrees have identical
    box geometry, as do the 90/270 pair. The detector can therefore select the
    dominant axis cheaply, while a recognizer probe resolves the remaining
    direction ambiguity.
    """

    signed = 0.0
    magnitude = 0.0
    count = 0
    for prediction in predictions:
        polygon = _polygon(prediction.get("polygon"))
        width, height = _axes(polygon)
        raw_score = prediction.get("score", 1.0)
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise DatasetError("detector prediction score must be numeric")
        score = float(raw_score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise DatasetError("detector prediction score must be between 0 and 1")
        aspect = max(-3.0, min(3.0, math.log(width / height)))
        if abs(aspect) < math.log(1.35):
            continue
        # Long, confident text lines carry more orientation evidence than small
        # square icons or punctuation boxes without allowing one huge region to
        # dominate the whole page.
        weight = max(0.05, score) * min(math.sqrt(width * height), 240.0)
        signed += weight * aspect
        magnitude += weight * abs(aspect)
        count += 1
    if count == 0 or magnitude <= 1e-9 or abs(signed) / magnitude < 0.25:
        return RIGHT_ANGLES
    return (0, 180) if signed > 0 else (90, 270)


def select_orientation(scores: Mapping[int, float], *, minimum_margin: float = 0.08) -> int:
    if not scores:
        return 0
    normalized: list[tuple[float, int]] = []
    for raw_rotation, raw_score in scores.items():
        rotation = int(raw_rotation)
        if rotation not in RIGHT_ANGLES:
            raise ValueError("orientation score rotation must be 0, 90, 180 or 270")
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError("orientation probe scores must be numeric")
        score = float(raw_score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("orientation probe scores must be between 0 and 1")
        normalized.append((score, rotation))
    normalized.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    best_score, best_rotation = normalized[0]
    second_score = normalized[1][0] if len(normalized) > 1 else 0.0
    if best_score - second_score < minimum_margin and 0 in scores:
        return 0
    return best_rotation


def _order_quad(points: Sequence[tuple[float, float]]) -> list[list[float]]:
    if len(points) != 4:
        raise ValueError("right-angle polygon transform requires four points")
    sums = [x + y for x, y in points]
    differences = [x - y for x, y in points]
    indices = (
        sums.index(min(sums)),
        differences.index(max(differences)),
        sums.index(max(sums)),
        differences.index(min(differences)),
    )
    if len(set(indices)) != 4:
        # A valid detector quad can be close to axis-aligned enough for equal
        # sums/differences. Fall back to y/x sorting and then left/right order.
        ordered = sorted(points, key=lambda point: (point[1], point[0]))
        top = sorted(ordered[:2], key=lambda point: point[0])
        bottom = sorted(ordered[2:], key=lambda point: point[0])
        return [[*top[0]], [*top[1]], [*bottom[1]], [*bottom[0]]]
    return [[float(points[index][0]), float(points[index][1])] for index in indices]


def transform_polygon_right_angle(
    polygon: object,
    *,
    width: int,
    height: int,
    degrees: int,
) -> list[list[float]]:
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    if degrees not in RIGHT_ANGLES:
        raise ValueError("right-angle rotation must be 0, 90, 180 or 270")
    points = _polygon(polygon)
    transformed: list[tuple[float, float]] = []
    for x, y in points:
        if degrees == 0:
            transformed.append((x, y))
        elif degrees == 90:
            transformed.append((float(height) - y, x))
        elif degrees == 180:
            transformed.append((float(width) - x, float(height) - y))
        else:
            transformed.append((y, float(width) - x))
    return _order_quad(transformed)


def canonicalize_predictions(
    predictions: Iterable[Mapping[str, Any]],
    *,
    width: int,
    height: int,
    degrees: int,
) -> tuple[list[dict[str, Any]], int, int]:
    transformed: list[dict[str, Any]] = []
    for raw in predictions:
        prediction = dict(raw)
        prediction["polygon"] = transform_polygon_right_angle(
            prediction.get("polygon"),
            width=width,
            height=height,
            degrees=degrees,
        )
        transformed.append(prediction)
    transformed.sort(key=lambda item: (
        min(float(point[1]) for point in item["polygon"]),
        min(float(point[0]) for point in item["polygon"]),
    ))
    if degrees in {90, 270}:
        return transformed, height, width
    return transformed, width, height


def rotate_image_right_angle(image: Any, degrees: int) -> Any:
    if degrees not in RIGHT_ANGLES:
        raise ValueError("right-angle rotation must be 0, 90, 180 or 270")
    if degrees == 0:
        return image
    import cv2

    code = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }[degrees]
    return cv2.rotate(image, code)


def probe_prediction_indices(
    predictions: Sequence[Mapping[str, object]],
    *,
    limit: int = 6,
) -> list[int]:
    if limit <= 0:
        raise ValueError("orientation probe limit must be positive")
    ranked: list[tuple[float, int]] = []
    for index, prediction in enumerate(predictions):
        polygon = _polygon(prediction.get("polygon"))
        width, height = _axes(polygon)
        score = float(prediction.get("score", 1.0))
        elongation = max(width, height) / min(width, height)
        ranked.append((score * min(elongation, 8.0) * math.sqrt(width * height), index))
    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return [index for _, index in ranked[:limit]]


__all__ = [
    "RIGHT_ANGLES",
    "canonicalize_predictions",
    "orientation_candidates",
    "probe_prediction_indices",
    "rotate_image_right_angle",
    "select_orientation",
    "transform_polygon_right_angle",
]