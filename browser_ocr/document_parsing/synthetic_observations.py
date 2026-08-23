from __future__ import annotations

import hashlib
import random
from typing import Any, Mapping, Sequence


SYNTHETIC_OBSERVATION_REVISION = 2


def _jitter_polygon(
    polygon: object,
    rng: random.Random,
    width: int,
    height: int,
) -> list[list[float]]:
    if not isinstance(polygon, list) or len(polygon) != 4:
        raise ValueError("truth node polygon must contain four points")
    max_dx = max(1.0, width * 0.0025)
    max_dy = max(1.0, height * 0.0025)
    dx = rng.uniform(-max_dx, max_dx)
    dy = rng.uniform(-max_dy, max_dy)
    return [
        [
            max(0.0, min(float(point[0]) + dx, float(width))),
            max(0.0, min(float(point[1]) + dy, float(height))),
        ]
        for point in polygon
    ]


def _bbox(polygon: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _rect(x1: float, y1: float, x2: float, y2: float) -> list[list[float]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _corrupt_text(text: str, rng: random.Random) -> str:
    if len(text) < 2 or rng.random() >= 0.10:
        return text
    index = rng.randrange(len(text))
    if rng.random() < 0.5:
        return text[:index] + text[index + 1 :]
    substitutions = {"1": "I", "0": "O", "정": "점", "회": "외", "일": "|"}
    replacement = substitutions.get(text[index], text[index])
    return text[:index] + replacement + text[index + 1 :]


def merge_one_visual_row_pair(observed: list[dict[str, Any]], rng: random.Random) -> bool:
    """Merge at most one OCR-like pair that plausibly belongs to one visual text row.

    A multiline paragraph can vertically contain a short table cell. Treating that
    containment as a same-row merge creates a giant observation spanning unrelated
    medication rows, so height comparability is an explicit part of the synthetic
    OCR contract rather than an incidental distance heuristic.
    """
    spatial = sorted(
        range(len(observed)),
        key=lambda index: (_bbox(observed[index]["polygon"])[1], _bbox(observed[index]["polygon"])[0]),
    )
    for left_index, right_index in zip(spatial, spatial[1:]):
        if rng.random() >= 0.08:
            continue
        left = observed[left_index]
        right = observed[right_index]
        lx1, ly1, lx2, ly2 = _bbox(left["polygon"])
        rx1, ry1, rx2, ry2 = _bbox(right["polygon"])
        left_height = max(1.0, ly2 - ly1)
        right_height = max(1.0, ry2 - ry1)
        smaller_height = min(left_height, right_height)
        larger_height = max(left_height, right_height)
        if larger_height / smaller_height > 1.8:
            continue
        vertical_overlap = max(0.0, min(ly2, ry2) - max(ly1, ry1))
        if vertical_overlap < 0.45 * smaller_height:
            continue
        gap = max(0.0, rx1 - lx2, lx1 - rx2)
        if gap > max(28.0, 1.5 * smaller_height):
            continue
        merged = {
            "text": f"{left['text']}{right['text']}",
            "recognition_score": min(float(left["recognition_score"]), float(right["recognition_score"])),
            "polygon": _rect(min(lx1, rx1), min(ly1, ry1), max(lx2, rx2), max(ly2, ry2)),
        }
        first, second = sorted((left_index, right_index), reverse=True)
        observed.pop(first)
        observed.pop(second)
        observed.append(merged)
        return True
    return False


def synthetic_observed_regions(sample: Mapping[str, Any], seed: int) -> list[dict[str, Any]]:
    document_id = str(sample["document_id"])
    digest = hashlib.sha256(f"{seed}:{document_id}:{SYNTHETIC_OBSERVATION_REVISION}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    width = int(sample["width"])
    height = int(sample["height"])
    observed: list[dict[str, Any]] = []
    for raw in sample["nodes"]:
        role = str(raw.get("semantic_role") or "other").strip()
        drop_rate = 0.06 if role in {"product", "dose", "frequency", "duration"} else 0.035
        if rng.random() < drop_rate:
            continue
        polygon = _jitter_polygon(raw.get("natural_text_polygon") or raw["polygon"], rng, width, height)
        text = _corrupt_text(str(raw.get("text") or ""), rng)
        confidence = round(rng.uniform(0.72, 0.995), 6)
        x1, y1, x2, y2 = _bbox(polygon)
        if len(text) >= 3 and x2 - x1 >= 36 and rng.random() < 0.10:
            split_at = max(1, min(len(text) - 1, len(text) // 2))
            split_x = x1 + (x2 - x1) * (split_at / len(text))
            observed.append(
                {"text": text[:split_at], "recognition_score": confidence, "polygon": _rect(x1, y1, split_x, y2)}
            )
            observed.append(
                {
                    "text": text[split_at:],
                    "recognition_score": max(0.0, confidence - 0.02),
                    "polygon": _rect(split_x, y1, x2, y2),
                }
            )
        else:
            observed.append({"text": text, "recognition_score": confidence, "polygon": polygon})

    merge_one_visual_row_pair(observed, rng)

    if rng.random() < 0.30:
        noise_x = rng.uniform(width * 0.05, width * 0.75)
        noise_y = rng.uniform(height * 0.70, height * 0.94)
        observed.append(
            {
                "text": rng.choice(["주의", "TEL", "보관", "문의"]),
                "recognition_score": round(rng.uniform(0.45, 0.88), 6),
                "polygon": _rect(noise_x, noise_y, min(width, noise_x + 80), min(height, noise_y + 28)),
            }
        )

    rng.shuffle(observed)
    for index, region in enumerate(observed, start=1):
        region["index"] = index
    return observed


__all__ = [
    "SYNTHETIC_OBSERVATION_REVISION",
    "merge_one_visual_row_pair",
    "synthetic_observed_regions",
]