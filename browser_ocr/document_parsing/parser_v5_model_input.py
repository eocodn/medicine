from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

from .parser_v5_contract import SEMANTIC_ROLES, validate_parser_v5_pair


BYTE_PAD = 0
BYTE_BOS = 1
BYTE_EOS = 2
BYTE_OFFSET = 3
BYTE_VOCAB_SIZE = 259
NODE_SCALAR_DIM = 12
RELATION_FEATURE_DIM = 14
PARSER_V5_ROLE_LABELS = (
    "product",
    "dose",
    "frequency",
    "duration",
    "instruction",
    "schedule",
    "header",
    "context",
    "other",
)
_ROLE_INDEX = {role: index for index, role in enumerate(PARSER_V5_ROLE_LABELS)}

if set(PARSER_V5_ROLE_LABELS) != SEMANTIC_ROLES:
    raise RuntimeError("Parser v5 model roles differ from the Parser v5 semantic contract")


@dataclass(frozen=True)
class ParserV5ModelInput:
    document_id: str
    node_ids: tuple[str, ...]
    token_ids: tuple[tuple[int, ...], ...]
    token_mask: tuple[tuple[bool, ...], ...]
    node_scalars: tuple[tuple[float, ...], ...]
    relation_features: tuple[tuple[tuple[float, ...], ...], ...]
    role_targets: tuple[tuple[float, ...], ...]
    role_mask: tuple[tuple[float, ...], ...]


def encode_text_bytes(text: str, *, max_bytes: int) -> tuple[tuple[int, ...], tuple[bool, ...]]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 4 <= max_bytes <= 512:
        raise ValueError("Parser v5 max_text_bytes must be between 4 and 512")
    normalized = unicodedata.normalize("NFC", str(text))
    payload = normalized.encode("utf-8")[: max_bytes - 2]
    tokens = [BYTE_BOS, *(byte + BYTE_OFFSET for byte in payload), BYTE_EOS]
    mask = [True] * len(tokens)
    padding = max_bytes - len(tokens)
    tokens.extend([BYTE_PAD] * padding)
    mask.extend([False] * padding)
    return tuple(tokens), tuple(mask)


def _bounds(polygon: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _fraction(text: str, predicate) -> float:
    return sum(1 for char in text if predicate(char)) / len(text) if text else 0.0


def _node_scalars(
    node: Mapping[str, object],
    *,
    width: float,
    height: float,
    max_text_bytes: int,
) -> tuple[float, ...]:
    polygon = node["polygon"]
    if not isinstance(polygon, Sequence):
        raise ValueError("Parser v5 node polygon must be a sequence")
    x1, y1, x2, y2 = _bounds(polygon)  # type: ignore[arg-type]
    box_width = max(1e-9, x2 - x1)
    box_height = max(1e-9, y2 - y1)
    text = str(node["text"])
    encoded_length = min(len(unicodedata.normalize("NFC", text).encode("utf-8")), max_text_bytes - 2)
    values = (
        ((x1 + x2) / 2.0) / width,
        ((y1 + y2) / 2.0) / height,
        box_width / width,
        box_height / height,
        min(box_width / box_height, 16.0) / 16.0,
        float(node["detector_confidence"]),
        float(node["recognizer_confidence"]),
        encoded_length / max(1, max_text_bytes - 2),
        _fraction(text, lambda char: unicodedata.category(char) == "Nd"),
        _fraction(text, lambda char: "가" <= char <= "힣"),
        _fraction(text, str.isspace),
        _fraction(text, lambda char: not char.isalnum() and not char.isspace()),
    )
    if len(values) != NODE_SCALAR_DIM:
        raise AssertionError("Parser v5 node scalar dimension changed unexpectedly")
    return values


def _overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    intersection = max(0.0, min(a2, b2) - max(a1, b1))
    return intersection / max(min(a2 - a1, b2 - b1), 1e-9)


def _relation_features(
    source: Mapping[str, object],
    target: Mapping[str, object],
    *,
    width: float,
    height: float,
    self_relation: bool,
) -> tuple[float, ...]:
    source_polygon = source["polygon"]
    target_polygon = target["polygon"]
    if not isinstance(source_polygon, Sequence) or not isinstance(target_polygon, Sequence):
        raise ValueError("Parser v5 relation polygon must be a sequence")
    sx1, sy1, sx2, sy2 = _bounds(source_polygon)  # type: ignore[arg-type]
    tx1, ty1, tx2, ty2 = _bounds(target_polygon)  # type: ignore[arg-type]
    scx, scy = (sx1 + sx2) / 2.0, (sy1 + sy2) / 2.0
    tcx, tcy = (tx1 + tx2) / 2.0, (ty1 + ty2) / 2.0
    dx = (tcx - scx) / width
    dy = (tcy - scy) / height
    distance = math.hypot(dx, dy)
    sw, sh = max(sx2 - sx1, 1e-9), max(sy2 - sy1, 1e-9)
    tw, th = max(tx2 - tx1, 1e-9), max(ty2 - ty1, 1e-9)
    values = (
        dx,
        dy,
        abs(dx),
        abs(dy),
        distance,
        _overlap(sy1, sy2, ty1, ty2),
        _overlap(sx1, sx2, tx1, tx2),
        1.0 if dx < 0 else 0.0,
        1.0 if dx > 0 else 0.0,
        1.0 if dy < 0 else 0.0,
        1.0 if dy > 0 else 0.0,
        max(-2.0, min(2.0, math.log(sw / tw))) / 2.0,
        max(-2.0, min(2.0, math.log(sh / th))) / 2.0,
        1.0 if self_relation else 0.0,
    )
    if len(values) != RELATION_FEATURE_DIM:
        raise AssertionError("Parser v5 relation feature dimension changed unexpectedly")
    return values


def _role_supervision(node: Mapping[str, object]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    raw_targets = node["targets"]
    if not isinstance(raw_targets, list):
        raise ValueError("Parser v5 node targets must be a list")
    targets = [0.0] * len(PARSER_V5_ROLE_LABELS)
    labeled_roles: set[str] = set()
    has_ambiguous = False
    for raw in raw_targets:
        if not isinstance(raw, Mapping):
            raise ValueError("Parser v5 node target must be an object")
        role = str(raw["semantic_role"])
        if raw["label_status"] == "labeled":
            targets[_ROLE_INDEX[role]] = 1.0
            labeled_roles.add(role)
        else:
            has_ambiguous = True
    if not has_ambiguous:
        mask = [1.0] * len(PARSER_V5_ROLE_LABELS)
    else:
        # With partial ambiguity, only explicitly labeled positive roles are
        # supervised; treating all other roles as negatives would reintroduce
        # the flat-label assumption that Parser v5 is designed to remove.
        mask = [1.0 if role in labeled_roles else 0.0 for role in PARSER_V5_ROLE_LABELS]
    return tuple(targets), tuple(mask)


def build_parser_v5_model_input(
    document: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    max_text_bytes: int = 96,
) -> ParserV5ModelInput:
    validate_parser_v5_pair(document, observation)  # type: ignore[arg-type]
    if isinstance(max_text_bytes, bool) or not isinstance(max_text_bytes, int) or not 4 <= max_text_bytes <= 512:
        raise ValueError("Parser v5 max_text_bytes must be between 4 and 512")
    width = float(document["width"])
    height = float(document["height"])
    raw_nodes = observation["nodes"]
    if not isinstance(raw_nodes, list):
        raise ValueError("Parser v5 observation nodes must be a list")
    nodes: list[Mapping[str, object]] = []
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            raise ValueError("Parser v5 observation node must be an object")
        nodes.append(raw)

    token_rows: list[tuple[int, ...]] = []
    mask_rows: list[tuple[bool, ...]] = []
    scalar_rows: list[tuple[float, ...]] = []
    role_targets: list[tuple[float, ...]] = []
    role_masks: list[tuple[float, ...]] = []
    for node in nodes:
        token_ids, token_mask = encode_text_bytes(str(node["text"]), max_bytes=max_text_bytes)
        token_rows.append(token_ids)
        mask_rows.append(token_mask)
        scalar_rows.append(_node_scalars(node, width=width, height=height, max_text_bytes=max_text_bytes))
        target, target_mask = _role_supervision(node)
        role_targets.append(target)
        role_masks.append(target_mask)

    pair_rows: list[tuple[tuple[float, ...], ...]] = []
    for source_index, source in enumerate(nodes):
        pair_rows.append(tuple(
            _relation_features(
                source,
                target,
                width=width,
                height=height,
                self_relation=source_index == target_index,
            )
            for target_index, target in enumerate(nodes)
        ))

    return ParserV5ModelInput(
        document_id=str(document["document_id"]),
        node_ids=tuple(str(node["node_id"]) for node in nodes),
        token_ids=tuple(token_rows),
        token_mask=tuple(mask_rows),
        node_scalars=tuple(scalar_rows),
        relation_features=tuple(pair_rows),
        role_targets=tuple(role_targets),
        role_mask=tuple(role_masks),
    )


def build_parser_v5_runtime_input(
    *,
    document_id: str,
    width: int | float,
    height: int | float,
    nodes: Sequence[Mapping[str, object]],
    max_text_bytes: int = 96,
) -> ParserV5ModelInput:
    """Build inference input without semantic truth, provenance, or labels."""

    document_id = str(document_id or "").strip()
    if not document_id:
        raise ValueError("Parser v5 runtime document_id is required")
    if isinstance(width, bool) or not isinstance(width, (int, float)) or float(width) <= 0:
        raise ValueError("Parser v5 runtime width must be positive")
    if isinstance(height, bool) or not isinstance(height, (int, float)) or float(height) <= 0:
        raise ValueError("Parser v5 runtime height must be positive")
    if isinstance(max_text_bytes, bool) or not isinstance(max_text_bytes, int) or not 4 <= max_text_bytes <= 512:
        raise ValueError("Parser v5 max_text_bytes must be between 4 and 512")
    page_width = float(width)
    page_height = float(height)
    normalized_nodes: list[Mapping[str, object]] = []
    seen: set[str] = set()
    required = {
        "node_id",
        "text",
        "detector_confidence",
        "recognizer_confidence",
        "polygon",
    }
    for index, raw in enumerate(nodes):
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError(f"Parser v5 runtime node {index} fields are invalid")
        node_id = str(raw.get("node_id") or "").strip()
        if not node_id or node_id in seen:
            raise ValueError("Parser v5 runtime node ids must be unique and non-empty")
        seen.add(node_id)
        for score_name in ("detector_confidence", "recognizer_confidence"):
            score = raw.get(score_name)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
                raise ValueError(f"Parser v5 runtime node {node_id} {score_name} must be in [0, 1]")
        polygon = raw.get("polygon")
        if not isinstance(polygon, Sequence) or len(polygon) != 4:
            raise ValueError(f"Parser v5 runtime node {node_id} polygon must contain four points")
        x1, y1, x2, y2 = _bounds(polygon)  # type: ignore[arg-type]
        if x1 < 0 or y1 < 0 or x2 > page_width or y2 > page_height or x2 <= x1 or y2 <= y1:
            raise ValueError(f"Parser v5 runtime node {node_id} polygon is outside the document")
        normalized_nodes.append(raw)

    token_rows: list[tuple[int, ...]] = []
    mask_rows: list[tuple[bool, ...]] = []
    scalar_rows: list[tuple[float, ...]] = []
    empty_targets = tuple(0.0 for _ in PARSER_V5_ROLE_LABELS)
    empty_mask = tuple(0.0 for _ in PARSER_V5_ROLE_LABELS)
    for node in normalized_nodes:
        token_ids, token_mask = encode_text_bytes(str(node["text"]), max_bytes=max_text_bytes)
        token_rows.append(token_ids)
        mask_rows.append(token_mask)
        scalar_rows.append(_node_scalars(node, width=page_width, height=page_height, max_text_bytes=max_text_bytes))

    pair_rows = tuple(
        tuple(
            _relation_features(
                source,
                target,
                width=page_width,
                height=page_height,
                self_relation=source_index == target_index,
            )
            for target_index, target in enumerate(normalized_nodes)
        )
        for source_index, source in enumerate(normalized_nodes)
    )
    return ParserV5ModelInput(
        document_id=document_id,
        node_ids=tuple(str(node["node_id"]) for node in normalized_nodes),
        token_ids=tuple(token_rows),
        token_mask=tuple(mask_rows),
        node_scalars=tuple(scalar_rows),
        relation_features=pair_rows,
        role_targets=tuple(empty_targets for _ in normalized_nodes),
        role_mask=tuple(empty_mask for _ in normalized_nodes),
    )


__all__ = [
    "BYTE_BOS",
    "BYTE_EOS",
    "BYTE_PAD",
    "BYTE_VOCAB_SIZE",
    "NODE_SCALAR_DIM",
    "PARSER_V5_ROLE_LABELS",
    "ParserV5ModelInput",
    "RELATION_FEATURE_DIM",
    "build_parser_v5_model_input",
    "build_parser_v5_runtime_input",
    "encode_text_bytes",
]