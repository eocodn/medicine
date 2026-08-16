from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from .dataset import DatasetError


_MIN_INTERNAL_GAP_HEIGHT_FRACTION = 0.5
_MAX_BLANK_INK_HEIGHT_FRACTION = 0.02
_MIN_CONTENT_HEIGHT_FRACTION = 0.25


def split_horizontal_ink_ranges(column_ink: Sequence[int], *, crop_height: int) -> list[tuple[int, int]]:
    """Split a horizontal OCR crop only across unusually large blank gaps.

    The detector can occasionally return one quad spanning adjacent fields.  A
    gap at least half a text-line height is much larger than ordinary Hangul
    glyph/word spacing, so it is useful as a text-agnostic segmentation cue.
    Tiny isolated fragments next to such a gap are trimmed instead of emitted
    as standalone OCR regions.
    """

    width = len(column_ink)
    if crop_height <= 0:
        raise ValueError("crop_height must be positive")
    if width <= 1:
        return [(0, width)]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in column_ink):
        raise ValueError("column_ink must contain non-negative integers")

    blank_limit = max(1, int(round(crop_height * _MAX_BLANK_INK_HEIGHT_FRACTION)))
    minimum_gap = max(1, int(math.ceil(crop_height * _MIN_INTERNAL_GAP_HEIGHT_FRACTION)))
    minimum_content = max(1, int(math.ceil(crop_height * _MIN_CONTENT_HEIGHT_FRACTION)))

    blank_runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, ink in enumerate(column_ink):
        is_blank = ink <= blank_limit
        if is_blank and start is None:
            start = index
        elif not is_blank and start is not None:
            if start > 0 and index < width and index - start >= minimum_gap:
                blank_runs.append((start, index))
            start = None

    if not blank_runs:
        return [(0, width)]

    boundaries = [0, *[(left + right) // 2 for left, right in blank_runs], width]
    ranges: list[tuple[int, int]] = []
    for left, right in zip(boundaries, boundaries[1:]):
        foreground = [index for index in range(left, right) if column_ink[index] > blank_limit]
        if not foreground:
            continue
        content_width = foreground[-1] - foreground[0] + 1
        if content_width < minimum_content:
            continue
        ranges.append((left, right))

    return ranges or [(0, width)]


def _point(point: Sequence[float], label: str) -> tuple[float, float]:
    if len(point) != 2:
        raise DatasetError(f"{label} must contain x/y")
    try:
        x = float(point[0])
        y = float(point[1])
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"{label} coordinates must be numeric") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise DatasetError(f"{label} coordinates must be finite")
    return x, y


def horizontal_subpolygon(
    polygon: Sequence[Sequence[float]],
    *,
    start: int,
    end: int,
    crop_width: int,
) -> list[list[float]]:
    """Map an exclusive horizontal crop range back onto an ordered source quad."""

    if len(polygon) != 4:
        raise DatasetError("detector prediction polygon must contain four points")
    if crop_width <= 0 or start < 0 or end <= start or end > crop_width:
        raise ValueError("invalid horizontal crop range")
    top_left, top_right, bottom_right, bottom_left = [
        _point(point, "detector prediction polygon point") for point in polygon
    ]

    def interpolate(left: tuple[float, float], right: tuple[float, float], fraction: float) -> list[float]:
        return [
            left[0] + (right[0] - left[0]) * fraction,
            left[1] + (right[1] - left[1]) * fraction,
        ]

    start_fraction = start / crop_width
    end_fraction = end / crop_width
    return [
        interpolate(top_left, top_right, start_fraction),
        interpolate(top_left, top_right, end_fraction),
        interpolate(bottom_left, bottom_right, end_fraction),
        interpolate(bottom_left, bottom_right, start_fraction),
    ]


def _is_tall_polygon(polygon: Sequence[Sequence[float]]) -> bool:
    points = [_point(point, "detector prediction polygon point") for point in polygon]

    def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    width = max(distance(points[0], points[1]), distance(points[2], points[3]))
    height = max(distance(points[0], points[3]), distance(points[1], points[2]))
    return height / max(width, 1e-9) >= 1.5


def _foreground_column_ink(crop: Any) -> list[int]:
    # Keep OpenCV/numpy lazy so the lightweight contract-test image can import
    # this module without carrying the full OCR runtime dependency set.
    import cv2
    import numpy as np

    if crop is None or getattr(crop, "ndim", None) != 3 or crop.shape[2] != 3:
        raise DatasetError("recognition crop must be a BGR image")
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8))
    return [int(value) for value in (mask > 0).sum(axis=0).tolist()]


def refine_prediction_crops(image: Any, predictions: Iterable[dict]) -> list[tuple[dict, Any]]:
    """Rectify detector boxes and generically split/trim obvious merged text lines."""

    from browser_ocr.detection.detector_benchmark import rectify_text_crop

    refined: list[tuple[dict, Any]] = []
    for raw_prediction in predictions:
        prediction = dict(raw_prediction)
        polygon = prediction.get("polygon")
        if not isinstance(polygon, list) or len(polygon) != 4:
            raise DatasetError("detector prediction polygon must contain four points")
        crop = rectify_text_crop(image, polygon)
        height, width = crop.shape[:2]
        ranges = [(0, width)]
        if not _is_tall_polygon(polygon) and width > 1:
            ranges = split_horizontal_ink_ranges(_foreground_column_ink(crop), crop_height=height)

        for start, end in ranges:
            refined_prediction = dict(prediction)
            if start != 0 or end != width:
                refined_prediction["polygon"] = horizontal_subpolygon(
                    polygon,
                    start=start,
                    end=end,
                    crop_width=width,
                )
            refined.append((refined_prediction, crop[:, start:end].copy()))
    return refined