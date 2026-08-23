from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .training_alignment import MODEL_ROLES


PAGE_NODE_ID = "__page__"
TEXT_HASH_DIM = 64
NODE_SCALAR_DIM = 17
NODE_FEATURE_DIM = TEXT_HASH_DIM + NODE_SCALAR_DIM
EDGE_FEATURE_DIM = 13
_TEXT_HASH_PREFIX = b"med-graph\0"
_FNV1A32_OFFSET = 2166136261
_FNV1A32_PRIME = 16777619
ROLE_LABELS = (
    "product",
    "product_label",
    "dose",
    "frequency",
    "duration",
    "instruction",
    "schedule",
    "header",
    "other",
)
_ROLE_INDEX = {role: index for index, role in enumerate(ROLE_LABELS)}

if set(ROLE_LABELS) != MODEL_ROLES:
    raise RuntimeError("document graph role labels differ from parser dataset roles")


@dataclass(frozen=True)
class GraphEncoderSpec:
    hidden_dim: int = 96
    layers: int = 2
    neighbor_count: int = 12
    pair_hidden_dim: int = 64

    def __post_init__(self) -> None:
        if not 16 <= self.hidden_dim <= 256:
            raise ValueError("hidden_dim must be between 16 and 256")
        if not 1 <= self.layers <= 6:
            raise ValueError("layers must be between 1 and 6")
        if not 1 <= self.neighbor_count <= 32:
            raise ValueError("neighbor_count must be between 1 and 32")
        if not 16 <= self.pair_hidden_dim <= 256:
            raise ValueError("pair_hidden_dim must be between 16 and 256")


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]
    features: tuple[float, ...]
    supervised: bool
    role_target: int | None


@dataclass(frozen=True)
class GraphEdge:
    source: int
    target: int
    kind: str
    features: tuple[float, ...]


@dataclass(frozen=True)
class RelationTarget:
    product_index: int
    field_index: int
    label: int
    features: tuple[float, ...]


@dataclass(frozen=True)
class DocumentGraph:
    document_id: str
    width: float
    height: float
    role_labels: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    relations: tuple[RelationTarget, ...]
    node_index: Mapping[str, int]


def graph_encoder_parameter_count(spec: GraphEncoderSpec) -> int:
    """Count the planned dense parameters without depending on a training runtime.

    The encoder uses one input projection, then per message-passing layer a
    self projection, neighbor projection and edge projection. The association
    head sees product/field hidden states plus their relative edge features.
    This keeps the deployment budget explicit before choosing an ONNX backend.
    """

    hidden = spec.hidden_dim
    pair_hidden = spec.pair_hidden_dim
    input_projection = NODE_FEATURE_DIM * hidden + hidden
    graph_layers = spec.layers * (
        hidden * hidden + hidden
        + hidden * hidden + hidden
        + EDGE_FEATURE_DIM * hidden + hidden
    )
    role_head = hidden * len(ROLE_LABELS) + len(ROLE_LABELS)
    association_head = (hidden * 2 + EDGE_FEATURE_DIM) * pair_hidden + pair_hidden + pair_hidden + 1
    return input_projection + graph_layers + role_head + association_head


def _compact(text: str) -> str:
    return "".join(text.split()).lower()


def _fraction(text: str, predicate) -> float:
    if not text:
        return 0.0
    return sum(1 for char in text if predicate(char)) / len(text)


def _hash_text(text: str) -> list[float]:
    normalized = _compact(text)
    features = [0.0] * TEXT_HASH_DIM
    grams: list[str] = []
    for width in (1, 2, 3):
        grams.extend(normalized[index : index + width] for index in range(max(0, len(normalized) - width + 1)))
    if not grams:
        return features
    for gram in grams:
        raw = _FNV1A32_OFFSET
        for byte in _TEXT_HASH_PREFIX + gram.encode("utf-8"):
            raw ^= byte
            raw = (raw * _FNV1A32_PRIME) & 0xFFFFFFFF
        bucket = raw % TEXT_HASH_DIM
        features[bucket] += -1.0 if raw & (1 << 8) else 1.0
    norm = math.sqrt(sum(value * value for value in features)) or 1.0
    return [value / norm for value in features]


def _polygon(value: object, node_id: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ValueError(f"{node_id}.polygon must contain four points")
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2:
            raise ValueError(f"{node_id}.polygon points must contain x/y")
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError(f"{node_id}.polygon coordinates must be numeric")
        if not math.isfinite(float(x)) or not math.isfinite(float(y)):
            raise ValueError(f"{node_id}.polygon coordinates must be finite")
        points.append((float(x), float(y)))
    return tuple(points)


def _bounds(polygon: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _geometry(polygon: Sequence[Sequence[float]]) -> tuple[float, float, float, float, float, float, float, float, float]:
    x1, y1, x2, y2 = _bounds(polygon)
    width = max(x2 - x1, 1e-9)
    height = max(y2 - y1, 1e-9)
    top_dx = float(polygon[1][0]) - float(polygon[0][0])
    top_dy = float(polygon[1][1]) - float(polygon[0][1])
    angle = math.atan2(top_dy, top_dx)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, width, height, x1, y1, x2, y2, angle


def _node_features(
    *,
    text: str,
    confidence: float,
    polygon: Sequence[Sequence[float]],
    document_width: float,
    document_height: float,
) -> tuple[float, ...]:
    cx, cy, width, height, x1, y1, x2, y2, angle = _geometry(polygon)
    length = len(text)
    compact = _compact(text)
    scalar = [
        cx / document_width,
        cy / document_height,
        width / document_width,
        height / document_height,
        min(width / max(height, 1e-9), 16.0) / 16.0,
        confidence,
        min(length / 64.0, 1.0),
        _fraction(text, lambda char: unicodedata.category(char) == "Nd"),
        _fraction(text, lambda char: "가" <= char <= "힣"),
        _fraction(text, str.isalpha),
        _fraction(text, str.isspace),
        _fraction(text, lambda char: not char.isalnum() and not char.isspace()),
        math.sin(angle),
        math.cos(angle),
        min(x1 / document_width, 1.0),
        min(y1 / document_height, 1.0),
        0.0,  # page-token indicator
    ]
    features = [*_hash_text(compact), *scalar]
    if len(features) != NODE_FEATURE_DIM:
        raise AssertionError("document graph node feature dimension changed unexpectedly")
    return tuple(float(value) for value in features)


def _page_features() -> tuple[float, ...]:
    features = [0.0] * NODE_FEATURE_DIM
    features[-1] = 1.0
    return tuple(features)


def _overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    intersection = max(0.0, min(a2, b2) - max(a1, b1))
    return intersection / max(min(a2 - a1, b2 - b1), 1e-9)


def _edge_features(
    source: GraphNode,
    target: GraphNode,
    *,
    document_width: float,
    document_height: float,
    page_edge: bool = False,
) -> tuple[float, ...]:
    if page_edge:
        values = [0.0] * EDGE_FEATURE_DIM
        values[-1] = 1.0
        return tuple(values)
    scx, scy, sw, sh, sx1, sy1, sx2, sy2, _ = _geometry(source.polygon)
    tcx, tcy, tw, th, tx1, ty1, tx2, ty2, _ = _geometry(target.polygon)
    dx = (scx - tcx) / document_width
    dy = (scy - tcy) / document_height
    distance = math.hypot(dx, dy)
    width_ratio = math.log(max(sw, 1e-9) / max(tw, 1e-9))
    height_ratio = math.log(max(sh, 1e-9) / max(th, 1e-9))
    values = [
        dx,
        dy,
        abs(dx),
        abs(dy),
        distance,
        _overlap(sy1, sy2, ty1, ty2),
        _overlap(sx1, sx2, tx1, tx2),
        1.0 if dx < 0 else 0.0,
        1.0 if dy < 0 else 0.0,
        max(-2.0, min(2.0, width_ratio)) / 2.0,
        max(-2.0, min(2.0, height_ratio)) / 2.0,
        abs(math.atan2(dy, dx)) / math.pi if distance else 0.0,
        0.0,
    ]
    if len(values) != EDGE_FEATURE_DIM:
        raise AssertionError("document graph edge feature dimension changed unexpectedly")
    return tuple(float(value) for value in values)


def _distance(source: GraphNode, target: GraphNode, width: float, height: float) -> float:
    scx, scy, *_ = _geometry(source.polygon)
    tcx, tcy, *_ = _geometry(target.polygon)
    return math.hypot((scx - tcx) / width, (scy - tcy) / height)


def relation_pair_features(
    graph: DocumentGraph,
    product_index: int,
    field_index: int,
) -> tuple[float, ...]:
    if not 1 <= product_index < len(graph.nodes) or not 1 <= field_index < len(graph.nodes):
        raise ValueError("relation pair indices must reference OCR nodes")
    if product_index == field_index:
        raise ValueError("relation pair endpoints must be different OCR nodes")
    return _edge_features(
        graph.nodes[product_index],
        graph.nodes[field_index],
        document_width=graph.width,
        document_height=graph.height,
    )


def build_document_graph(document: Mapping[str, Any], *, neighbor_count: int = 12) -> DocumentGraph:
    if not 1 <= neighbor_count <= 32:
        raise ValueError("neighbor_count must be between 1 and 32")
    document_id = str(document.get("document_id") or "")
    if not document_id:
        raise ValueError("document_id is required")
    width = document.get("width")
    height = document.get("height")
    if isinstance(width, bool) or not isinstance(width, (int, float)) or float(width) <= 0:
        raise ValueError("document width must be positive")
    if isinstance(height, bool) or not isinstance(height, (int, float)) or float(height) <= 0:
        raise ValueError("document height must be positive")
    document_width = float(width)
    document_height = float(height)
    observation = document.get("observation")
    if not isinstance(observation, Mapping) or not isinstance(observation.get("nodes"), list):
        raise ValueError("document observation nodes are required")

    page = GraphNode(
        node_id=PAGE_NODE_ID,
        text="",
        confidence=1.0,
        polygon=((0.0, 0.0), (document_width, 0.0), (document_width, document_height), (0.0, document_height)),
        features=_page_features(),
        supervised=False,
        role_target=None,
    )
    nodes: list[GraphNode] = [page]
    node_index: dict[str, int] = {PAGE_NODE_ID: 0}
    for raw in observation["nodes"]:
        if not isinstance(raw, Mapping):
            raise ValueError("document observation contains a non-object node")
        node_id = str(raw.get("node_id") or "")
        if not node_id or node_id == PAGE_NODE_ID or node_id in node_index:
            raise ValueError("document observation node ids must be unique and cannot use the page id")
        text = str(raw.get("text") or "")
        confidence_raw = raw.get("confidence")
        if isinstance(confidence_raw, bool) or not isinstance(confidence_raw, (int, float)):
            raise ValueError(f"{node_id}.confidence must be numeric")
        confidence = float(confidence_raw)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{node_id}.confidence must be between 0 and 1")
        polygon = _polygon(raw.get("polygon"), node_id)
        status = str(raw.get("label_status") or "")
        raw_role = raw.get("semantic_role")
        supervised = status == "labeled" and isinstance(raw_role, str) and raw_role in _ROLE_INDEX
        role_target = _ROLE_INDEX[str(raw_role)] if supervised else None
        node_index[node_id] = len(nodes)
        nodes.append(GraphNode(
            node_id=node_id,
            text=text,
            confidence=confidence,
            polygon=polygon,
            features=_node_features(
                text=text,
                confidence=confidence,
                polygon=polygon,
                document_width=document_width,
                document_height=document_height,
            ),
            supervised=supervised,
            role_target=role_target,
        ))

    edges: list[GraphEdge] = []
    ocr_indices = list(range(1, len(nodes)))
    for target_index in ocr_indices:
        target = nodes[target_index]
        ranked = sorted(
            (index for index in ocr_indices if index != target_index),
            key=lambda index: (_distance(nodes[index], target, document_width, document_height), nodes[index].node_id),
        )
        for source_index in ranked[:neighbor_count]:
            edges.append(GraphEdge(
                source=source_index,
                target=target_index,
                kind="spatial",
                features=_edge_features(
                    nodes[source_index],
                    target,
                    document_width=document_width,
                    document_height=document_height,
                ),
            ))
        edges.append(GraphEdge(
            source=0,
            target=target_index,
            kind="page",
            features=_edge_features(page, target, document_width=document_width, document_height=document_height, page_edge=True),
        ))
        edges.append(GraphEdge(
            source=target_index,
            target=0,
            kind="page",
            features=_edge_features(target, page, document_width=document_width, document_height=document_height, page_edge=True),
        ))

    raw_relations = document.get("relations")
    if not isinstance(raw_relations, list):
        raise ValueError("document relations must be a list")
    relations: list[RelationTarget] = []
    for raw in raw_relations:
        if not isinstance(raw, Mapping):
            raise ValueError("document relation must be an object")
        product_id = str(raw.get("product_node_id") or "")
        field_id = str(raw.get("field_node_id") or "")
        if product_id not in node_index or field_id not in node_index:
            raise ValueError("document relation references an unknown node")
        label = raw.get("label")
        if label not in {"same_medication", "different_medication"}:
            raise ValueError("document relation label is unsupported")
        relations.append(RelationTarget(
            product_index=node_index[product_id],
            field_index=node_index[field_id],
            label=1 if label == "same_medication" else 0,
            features=_edge_features(
                nodes[node_index[product_id]],
                nodes[node_index[field_id]],
                document_width=document_width,
                document_height=document_height,
            ),
        ))

    return DocumentGraph(
        document_id=document_id,
        width=document_width,
        height=document_height,
        role_labels=ROLE_LABELS,
        nodes=tuple(nodes),
        edges=tuple(edges),
        relations=tuple(relations),
        node_index=dict(node_index),
    )


__all__ = [
    "DocumentGraph",
    "EDGE_FEATURE_DIM",
    "GraphEdge",
    "GraphEncoderSpec",
    "GraphNode",
    "NODE_FEATURE_DIM",
    "PAGE_NODE_ID",
    "ROLE_LABELS",
    "RelationTarget",
    "build_document_graph",
    "graph_encoder_parameter_count",
    "relation_pair_features",
]