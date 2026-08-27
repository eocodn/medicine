from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any, Mapping

from .parser_v5_contract import validate_parser_v5_document, validate_parser_v5_pair


@dataclass(frozen=True)
class ObservationProfile:
    text_corruption_rate: float = 0.12
    drop_rate: float = 0.04
    duplicate_rate: float = 0.03
    split_rate: float = 0.05
    merge_rate: float = 0.07
    geometry_jitter: float = 0.004
    false_positive_count: tuple[int, int] = (0, 4)
    reading_order_shuffle_rate: float = 0.04

    def __post_init__(self) -> None:
        for name in (
            "text_corruption_rate",
            "drop_rate",
            "duplicate_rate",
            "split_rate",
            "merge_rate",
            "reading_order_shuffle_rate",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"parser v5 observation {name} must be in [0, 1]")
        if (
            isinstance(self.geometry_jitter, bool)
            or not isinstance(self.geometry_jitter, (int, float))
            or not 0.0 <= float(self.geometry_jitter) <= 0.25
        ):
            raise ValueError("parser v5 observation geometry_jitter must be in [0, 0.25]")
        low, high = self.false_positive_count
        if (
            isinstance(low, bool)
            or isinstance(high, bool)
            or not isinstance(low, int)
            or not isinstance(high, int)
            or low < 0
            or high < low
        ):
            raise ValueError("parser v5 observation false_positive_count must be a non-negative inclusive range")


def _rng(document_id: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"parser-v5-observation:{seed}:{document_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _bbox(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _rect(x1: float, y1: float, x2: float, y2: float) -> list[list[float]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _jitter_polygon(
    polygon: list[list[float]],
    *,
    rng: random.Random,
    magnitude: float,
    width: int,
    height: int,
) -> list[list[float]]:
    if magnitude == 0.0:
        return [[float(x), float(y)] for x, y in polygon]
    dx = rng.uniform(-magnitude * width, magnitude * width)
    dy = rng.uniform(-magnitude * height, magnitude * height)
    x1, y1, x2, y2 = _bbox(polygon)
    box_width = max(1.0, x2 - x1)
    box_height = max(1.0, y2 - y1)
    nx1 = max(0.0, min(x1 + dx, width - box_width))
    ny1 = max(0.0, min(y1 + dy, height - box_height))
    return _rect(nx1, ny1, nx1 + box_width, ny1 + box_height)


def _target(span: Mapping[str, Any], *, label_status: str = "labeled") -> dict[str, Any]:
    return {
        "source_span_id": str(span["span_id"]),
        "semantic_role": str(span["semantic_role"]),
        "association_group": span.get("association_group"),
        "label_status": label_status,
    }


def _initial_node(span: Mapping[str, Any], *, rng: random.Random) -> dict[str, Any]:
    return {
        "text": str(span["text"]),
        "detector_confidence": rng.uniform(0.84, 0.999),
        "recognizer_confidence": rng.uniform(0.82, 0.999),
        "polygon": [[float(x), float(y)] for x, y in span["polygon"]],
        "source_span_ids": [str(span["span_id"])],
        "targets": [_target(span)],
        "operation": "identity",
        "source_reading_order": int(span["reading_order"]),
    }


def _split_node(node: Mapping[str, Any], *, rng: random.Random) -> tuple[dict[str, Any], dict[str, Any]] | None:
    text = str(node["text"])
    if len(text) < 2:
        return None
    split_at = rng.randint(1, len(text) - 1)
    x1, y1, x2, y2 = _bbox(node["polygon"])
    fraction = split_at / len(text)
    middle = x1 + (x2 - x1) * fraction
    left = dict(node)
    right = dict(node)
    left.update(text=text[:split_at], polygon=_rect(x1, y1, middle, y2), operation="split")
    right.update(text=text[split_at:], polygon=_rect(middle, y1, x2, y2), operation="split")
    left["source_span_ids"] = list(node["source_span_ids"])
    right["source_span_ids"] = list(node["source_span_ids"])
    left["targets"] = [dict(target) for target in node["targets"]]
    right["targets"] = [dict(target) for target in node["targets"]]
    return left, right


def _merge_nodes(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    lx1, ly1, lx2, ly2 = _bbox(left["polygon"])
    rx1, ry1, rx2, ry2 = _bbox(right["polygon"])
    source_ids = list(dict.fromkeys([*left["source_span_ids"], *right["source_span_ids"]]))
    targets_by_span: dict[str, dict[str, Any]] = {}
    for raw in [*left["targets"], *right["targets"]]:
        target = dict(raw)
        targets_by_span[str(target["source_span_id"])] = target
    return {
        "text": f"{left['text']} {right['text']}",
        "detector_confidence": min(float(left["detector_confidence"]), float(right["detector_confidence"])),
        "recognizer_confidence": min(float(left["recognizer_confidence"]), float(right["recognizer_confidence"])),
        "polygon": _rect(min(lx1, rx1), min(ly1, ry1), max(lx2, rx2), max(ly2, ry2)),
        "source_span_ids": source_ids,
        "targets": list(targets_by_span.values()),
        "operation": "merge",
        "source_reading_order": min(int(left["source_reading_order"]), int(right["source_reading_order"])),
    }


def _single_role(node: Mapping[str, Any]) -> str | None:
    roles = {str(target["semantic_role"]) for target in node["targets"]}
    return next(iter(roles)) if len(roles) == 1 else None


def _single_group(node: Mapping[str, Any]) -> str | None:
    groups = {target.get("association_group") for target in node["targets"]}
    return next(iter(groups)) if len(groups) == 1 else None


def _merge_priority_pair(nodes: list[dict[str, Any]], *, rng: random.Random, rate: float) -> list[dict[str, Any]]:
    if rate <= 0.0:
        return nodes
    for left_index, left in enumerate(nodes):
        if _single_role(left) not in {"dose", "frequency"}:
            continue
        for right_index in range(left_index + 1, min(len(nodes), left_index + 4)):
            right = nodes[right_index]
            if {_single_role(left), _single_role(right)} != {"dose", "frequency"}:
                continue
            group = _single_group(left)
            if group is None or group != _single_group(right):
                continue
            if rng.random() > rate:
                return nodes
            merged = _merge_nodes(left, right)
            result = [node for index, node in enumerate(nodes) if index not in {left_index, right_index}]
            result.append(merged)
            result.sort(key=lambda node: (int(node["source_reading_order"]), _bbox(node["polygon"])[0]))
            return result
    return nodes


def _merge_adjacent_pairs(nodes: list[dict[str, Any]], *, rng: random.Random, rate: float) -> list[dict[str, Any]]:
    if rate <= 0.0 or len(nodes) < 2:
        return nodes
    ordered = sorted(nodes, key=lambda node: (int(node["source_reading_order"]), _bbox(node["polygon"])[0]))
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(ordered):
        left = ordered[index]
        if index + 1 >= len(ordered):
            result.append(left)
            break
        right = ordered[index + 1]
        lx1, ly1, lx2, ly2 = _bbox(left["polygon"])
        rx1, ry1, rx2, ry2 = _bbox(right["polygon"])
        smaller_height = min(max(1.0, ly2 - ly1), max(1.0, ry2 - ry1))
        vertical_overlap = max(0.0, min(ly2, ry2) - max(ly1, ry1))
        horizontal_gap = max(0.0, rx1 - lx2, lx1 - rx2)
        same_visual_row = vertical_overlap >= 0.45 * smaller_height and horizontal_gap <= 2.0 * smaller_height
        if same_visual_row and rng.random() < rate:
            result.append(_merge_nodes(left, right))
            index += 2
            continue
        result.append(left)
        index += 1
    return result


def _corrupt_text(text: str, *, rng: random.Random) -> str:
    if not text:
        return text
    substitutions = {"1": "I", "0": "O", "정": "점", "회": "외", "일": "|", "아": "어"}
    index = rng.randrange(len(text))
    replacement = substitutions.get(text[index])
    if replacement is not None:
        candidate = text[:index] + replacement + text[index + 1 :]
        return candidate if candidate.strip() else "?"
    if len(text) > 1 and rng.random() < 0.5:
        candidate = text[:index] + text[index + 1 :]
        return candidate if candidate.strip() else "?"
    candidate = text[:index] + "?" + text[index + 1 :]
    return candidate if candidate.strip() else "?"


def _false_positive(*, rng: random.Random, width: int, height: int) -> dict[str, Any]:
    text = rng.choice(("···", "12345", "확인", "2026-08", "합계"))
    box_width = rng.uniform(60.0, 180.0)
    box_height = rng.uniform(24.0, 44.0)
    x = rng.uniform(0.0, max(0.0, width - box_width))
    y = rng.uniform(0.0, max(0.0, height - box_height))
    return {
        "text": text,
        "detector_confidence": rng.uniform(0.20, 0.82),
        "recognizer_confidence": rng.uniform(0.15, 0.80),
        "polygon": _rect(x, y, x + box_width, y + box_height),
        "source_span_ids": [],
        "targets": [],
        "operation": "false_positive",
        "source_reading_order": 1_000_000 + rng.randrange(1_000_000),
    }


def simulate_observations(
    document: Mapping[str, Any],
    *,
    seed: int,
    profile: ObservationProfile | None = None,
) -> dict[str, Any]:
    validate_parser_v5_document(document)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("parser v5 observation seed must be an integer")
    selected = profile or ObservationProfile()
    rng = _rng(str(document["document_id"]), seed)
    width = int(document["width"])
    height = int(document["height"])

    nodes: list[dict[str, Any]] = []
    for span in sorted(document["spans"], key=lambda item: int(item["reading_order"])):
        if rng.random() < selected.drop_rate:
            continue
        node = _initial_node(span, rng=rng)
        if rng.random() < selected.split_rate:
            split = _split_node(node, rng=rng)
            if split is not None:
                nodes.extend(split)
                continue
        nodes.append(node)
        if rng.random() < selected.duplicate_rate:
            duplicate = dict(node)
            duplicate["source_span_ids"] = list(node["source_span_ids"])
            duplicate["targets"] = [dict(target) for target in node["targets"]]
            duplicate["operation"] = "duplicate"
            nodes.append(duplicate)

    nodes.sort(key=lambda node: (int(node["source_reading_order"]), _bbox(node["polygon"])[0]))
    nodes = _merge_priority_pair(nodes, rng=rng, rate=float(selected.merge_rate))
    nodes = _merge_adjacent_pairs(nodes, rng=rng, rate=float(selected.merge_rate) * 0.5)

    for node in nodes:
        node["polygon"] = _jitter_polygon(
            node["polygon"],
            rng=rng,
            magnitude=float(selected.geometry_jitter),
            width=width,
            height=height,
        )
        if rng.random() < selected.text_corruption_rate:
            node["text"] = _corrupt_text(str(node["text"]), rng=rng)
            node["recognizer_confidence"] = min(float(node["recognizer_confidence"]), rng.uniform(0.35, 0.88))

    for _ in range(rng.randint(*selected.false_positive_count)):
        nodes.append(_false_positive(rng=rng, width=width, height=height))

    if nodes and rng.random() < selected.reading_order_shuffle_rate:
        rng.shuffle(nodes)
    else:
        nodes.sort(key=lambda node: (_bbox(node["polygon"])[1], _bbox(node["polygon"])[0], int(node["source_reading_order"])))

    for index, node in enumerate(nodes, start=1):
        node["node_id"] = f"obs-{index:04d}"
        node.pop("source_reading_order", None)

    observation = {
        "document_id": str(document["document_id"]),
        "profile_revision": 1,
        "nodes": nodes,
    }
    validate_parser_v5_pair(document, observation)
    return observation


__all__ = ["ObservationProfile", "simulate_observations"]