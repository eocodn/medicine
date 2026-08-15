from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Sequence


TEXT_HASH_DIM = 48
NODE_NUMERIC_DIM = 16
NODE_FEATURE_DIM = NODE_NUMERIC_DIM + TEXT_HASH_DIM

DOSE_VALUE = re.compile(r"^(\d+(?:\.\d+)?)\s*(정|tablet|캡슐|capsule|포|mL|ml)$", re.I)
PACKET_TABLET = re.compile(r"^(\d+(?:\.\d+)?)\s*포\(정\)$")
FREQUENCY_VALUE = re.compile(r"^(\d+)\s*회$")
DURATION_VALUE = re.compile(r"^(\d+)\s*일(?:분)?$")
PRODUCT_SUFFIX = re.compile(r"(?:정|캡슐|정제|시럽|액|산|과립|주)$")


@dataclass(frozen=True)
class LayoutNode:
    box_id: str
    text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]
    role: str | None = None
    group: str | None = None


@dataclass(frozen=True)
class LabeledDocument:
    sample_id: str
    width: float
    height: float
    nodes: tuple[LayoutNode, ...]
    layout_family: str
    capture_profile: str


@dataclass(frozen=True)
class SemanticExample:
    text: str
    role: str


def bounds(node: LayoutNode) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in node.polygon]
    ys = [float(point[1]) for point in node.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def center(node: LayoutNode) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bounds(node)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, x2 - x1, y2 - y1


def compact(text: str) -> str:
    return "".join(text.split())


def _fraction(text: str, predicate) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if predicate(char)) / len(text)


def _hashed_text(text: str) -> list[float]:
    normalized = compact(text).lower()
    values = [0.0] * TEXT_HASH_DIM
    grams: list[str] = []
    for width in (1, 2, 3):
        grams.extend(normalized[index : index + width] for index in range(max(0, len(normalized) - width + 1)))
    if not grams:
        return values
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8, person=b"med-layout").digest()
        raw = int.from_bytes(digest, "big")
        index = raw % TEXT_HASH_DIM
        values[index] += -1.0 if (raw >> 8) & 1 else 1.0
    scale = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / scale for value in values]


def node_features(node: LayoutNode, *, width: float, height: float) -> list[float]:
    if width <= 0 or height <= 0:
        raise ValueError("document width and height must be positive")
    cx, cy, box_width, box_height = center(node)
    text = node.text.strip()
    value = compact(text)
    length = len(text)
    numeric = [
        cx / width,
        cy / height,
        box_width / width,
        box_height / height,
        min(box_width / max(box_height, 1.0), 12.0) / 12.0,
        max(0.0, min(float(node.confidence), 1.0)),
        min(length / 32.0, 1.0),
        _fraction(text, str.isdigit),
        _fraction(text, lambda char: "가" <= char <= "힣"),
        1.0 if "." in text else 0.0,
        1.0 if (DOSE_VALUE.fullmatch(value) or PACKET_TABLET.fullmatch(value)) else 0.0,
        1.0 if FREQUENCY_VALUE.fullmatch(value) else 0.0,
        1.0 if DURATION_VALUE.fullmatch(value) else 0.0,
        1.0 if PRODUCT_SUFFIX.search(value) else 0.0,
        1.0 if (":" in text or "：" in text) else 0.0,
        _fraction(text, str.isspace),
    ]
    features = numeric + _hashed_text(text)
    if len(features) != NODE_FEATURE_DIM:
        raise AssertionError("node feature dimension changed unexpectedly")
    return [float(item) for item in features]


def edge_features(
    product: LayoutNode,
    field: LayoutNode,
    *,
    width: float,
    height: float,
    field_role: str,
    candidate_products: Sequence[LayoutNode],
) -> list[float]:
    pcx, pcy, pw, ph = center(product)
    fcx, fcy, fw, fh = center(field)
    px1, py1, px2, py2 = bounds(product)
    fx1, fy1, fx2, fy2 = bounds(field)
    vertical_intersection = max(0.0, min(py2, fy2) - max(py1, fy1))
    horizontal_intersection = max(0.0, min(px2, fx2) - max(px1, fx1))
    vertical_overlap = vertical_intersection / max(min(ph, fh), 1.0)
    horizontal_overlap = horizontal_intersection / max(min(pw, fw), 1.0)
    dx = (fcx - pcx) / width
    dy = (fcy - pcy) / height
    product_centers = [(candidate, *center(candidate)[:2]) for candidate in candidate_products]
    nearest_y = min(product_centers, key=lambda item: (abs(item[2] - fcy), item[0].box_id))[0]
    nearest_distance = min(
        product_centers,
        key=lambda item: (math.hypot((item[1] - fcx) / width, (item[2] - fcy) / height), item[0].box_id),
    )[0]
    lower_y, upper_y = sorted((pcy, fcy))
    products_between = sum(
        1
        for candidate, _, candidate_y in product_centers
        if candidate.box_id != product.box_id and lower_y < candidate_y < upper_y
    )
    ordered_products = sorted(candidate_products, key=lambda item: (center(item)[1], center(item)[0], item.box_id))
    rank = ordered_products.index(product) / max(len(ordered_products) - 1, 1)
    return [
        dx,
        dy,
        abs(dx),
        abs(dy),
        math.hypot(dx, dy),
        vertical_overlap,
        horizontal_overlap,
        1.0 if fcx >= pcx else 0.0,
        1.0 if fcy >= pcy else 0.0,
        min(pw / max(fw, 1.0), 8.0) / 8.0,
        min(ph / max(fh, 1.0), 8.0) / 8.0,
        max(0.0, min(product.confidence, 1.0)),
        max(0.0, min(field.confidence, 1.0)),
        1.0 if field_role == "dose" else 0.0,
        1.0 if field_role == "frequency" else 0.0,
        1.0 if field_role == "duration" else 0.0,
        min(len(candidate_products) / 12.0, 1.0),
        1.0 if len(candidate_products) == 1 else 0.0,
        1.0 if nearest_y.box_id == product.box_id else 0.0,
        1.0 if nearest_distance.box_id == product.box_id else 0.0,
        products_between / max(len(candidate_products) - 1, 1),
        rank,
    ]