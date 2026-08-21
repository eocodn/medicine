from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .dataset import DatasetError


def _polygon_key(prediction: dict) -> tuple[float, float]:
    polygon = prediction.get("polygon")
    if not isinstance(polygon, list) or len(polygon) != 4:
        raise DatasetError("detector prediction polygon must contain four points")
    points: list[tuple[float, float]] = []
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2:
            raise DatasetError("detector prediction polygon point must contain x/y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise DatasetError("detector prediction polygon coordinates must be numeric") from exc
        points.append((x, y))
    return min(y for _, y in points), min(x for x, _ in points)


def sort_text_predictions(predictions: Iterable[dict]) -> list[dict]:
    """Return a deterministic top-to-bottom, then left-to-right box order."""

    return sorted((dict(item) for item in predictions), key=_polygon_key)


def _canonical_path(path: str) -> str:
    return str(Path(path).resolve(strict=False))


def parse_recognition_rows(text: str, expected_crop_paths: Iterable[str]) -> dict[str, dict[str, object]]:
    expected = list(expected_crop_paths)
    canonical_to_expected: dict[str, str] = {}
    for raw in expected:
        canonical = _canonical_path(raw)
        if canonical in canonical_to_expected:
            raise DatasetError(f"duplicate expected crop path: {raw}")
        canonical_to_expected[canonical] = raw

    recognized: dict[str, dict[str, object]] = {}
    seen_canonical: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        parts = raw_line.split("\t")
        if len(parts) < 3:
            raise DatasetError(f"recognition row {line_number} must contain path, text, and score")
        path = parts[0]
        value = "\t".join(parts[1:-1])
        try:
            score = float(parts[-1])
        except ValueError as exc:
            raise DatasetError(f"recognition row {line_number} score is not numeric") from exc
        canonical = _canonical_path(path)
        if canonical not in canonical_to_expected:
            raise DatasetError(f"unexpected recognition result: {path}")
        if canonical in seen_canonical:
            raise DatasetError(f"duplicate recognition result: {path}")
        seen_canonical.add(canonical)
        recognized[canonical_to_expected[canonical]] = {"text": value, "score": score}

    for raw in expected:
        if raw not in recognized:
            raise DatasetError(f"missing recognition result: {raw}")
    return recognized


def build_document_regions(
    predictions: Iterable[dict],
    crop_paths: Iterable[str],
    recognized: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    prediction_list = list(predictions)
    crop_list = list(crop_paths)
    if len(prediction_list) != len(crop_list):
        raise DatasetError("detection and crop counts differ")

    regions: list[dict[str, object]] = []
    for index, (prediction, crop_path) in enumerate(zip(prediction_list, crop_list, strict=True), start=1):
        if crop_path not in recognized:
            raise DatasetError(f"missing recognition result: {crop_path}")
        recognition = recognized[crop_path]
        regions.append(
            {
                "index": index,
                "polygon": prediction["polygon"],
                "detection_score": float(prediction["score"]),
                "crop": Path(crop_path).name,
                "text": str(recognition["text"]),
                "recognition_score": float(recognition["score"]),
            }
        )
    return regions
