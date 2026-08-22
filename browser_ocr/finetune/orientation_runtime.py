from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .crop_refinement import refine_prediction_crops
from .orientation import (
    canonicalize_predictions,
    orientation_candidates,
    probe_prediction_indices,
    rotate_image_right_angle,
    select_orientation,
)


ProbeScorer = Callable[[Mapping[int, Sequence[Any]]], Mapping[int, float]]


def resolve_page_orientation(
    image: Any,
    predictions: Sequence[Mapping[str, Any]],
    *,
    probe_scorer: ProbeScorer,
    probe_limit: int = 6,
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    """Canonicalize a detected page without running the detector again.

    Detector geometry reduces the right-angle search to one 180-degree pair in
    the common case. A bounded set of rectified crops is then recognized under
    each candidate; only those small probe crops are retained simultaneously.
    The selected full raster is rotated once and the original detector polygons
    are transformed into the same canonical coordinate frame.
    """

    if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
        raise ValueError("orientation input must be an image array")
    source_height, source_width = int(image.shape[0]), int(image.shape[1])
    if source_width <= 0 or source_height <= 0:
        raise ValueError("orientation input image dimensions must be positive")
    raw_predictions = [dict(prediction) for prediction in predictions]
    candidates = orientation_candidates(raw_predictions)
    if not raw_predictions:
        return image, [], {
            "method": "detector_axis_recognizer_probe_v1",
            "candidates": list(candidates),
            "probe_scores": {},
            "probe_regions": 0,
            "applied_rotation_degrees": 0,
        }

    selected_indices = probe_prediction_indices(raw_predictions, limit=probe_limit)
    probes: dict[int, list[Any]] = {}
    probe_count = 0
    for rotation in candidates:
        rotated_image = rotate_image_right_angle(image, rotation)
        selected_predictions = [raw_predictions[index] for index in selected_indices]
        transformed, _, _ = canonicalize_predictions(
            selected_predictions,
            width=source_width,
            height=source_height,
            degrees=rotation,
        )
        refined = refine_prediction_crops(rotated_image, transformed)
        crops = [crop for _, crop in refined]
        probes[rotation] = crops
        probe_count += len(crops)

    scores = dict(probe_scorer(probes)) if probe_count else {}
    if set(scores) != set(candidates):
        raise ValueError("orientation probe scorer must return exactly one score per candidate")
    selected_rotation = select_orientation(scores)
    canonical_image = rotate_image_right_angle(image, selected_rotation)
    canonical_predictions, canonical_width, canonical_height = canonicalize_predictions(
        raw_predictions,
        width=source_width,
        height=source_height,
        degrees=selected_rotation,
    )
    if canonical_image.shape[1] != canonical_width or canonical_image.shape[0] != canonical_height:
        raise ValueError("orientation raster and polygon dimensions disagree")
    return canonical_image, canonical_predictions, {
        "method": "detector_axis_recognizer_probe_v1",
        "candidates": list(candidates),
        "probe_scores": {str(rotation): round(float(scores[rotation]), 6) for rotation in candidates},
        "probe_regions": probe_count,
        "applied_rotation_degrees": selected_rotation,
    }


__all__ = ["resolve_page_orientation"]