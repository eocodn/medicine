from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path
from typing import Mapping, Sequence

from .learned_features import (
    DOSE_VALUE as _DOSE_VALUE,
    DURATION_VALUE as _DURATION_VALUE,
    FREQUENCY_VALUE as _FREQUENCY_VALUE,
    NODE_FEATURE_DIM,
    PACKET_TABLET as _PACKET_TABLET,
    LayoutNode,
    LabeledDocument,
    SemanticExample,
    center as _center,
    compact as _compact,
    edge_features as _edge_features,
    node_features,
)


MODEL_ID = "hashed_layout_linear_v2"
ROLE_LABELS = ("product", "dose", "frequency", "duration", "other")
_ROLE_INDEX = {label: index for index, label in enumerate(ROLE_LABELS)}


def _softmax(scores: Sequence[float]) -> list[float]:
    peak = max(scores)
    exps = [math.exp(score - peak) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _dot(weights: Sequence[float], features: Sequence[float]) -> float:
    return sum(weight * feature for weight, feature in zip(weights, features, strict=True))


def _node_examples(documents: Sequence[LabeledDocument]) -> list[tuple[list[float], int]]:
    examples: list[tuple[list[float], int]] = []
    for document in documents:
        for node in document.nodes:
            role = node.role if node.role in _ROLE_INDEX else "other"
            examples.append((node_features(node, width=document.width, height=document.height), _ROLE_INDEX[role]))
    return examples


def _semantic_node_examples(examples: Sequence[SemanticExample]) -> list[tuple[list[float], int]]:
    encoded: list[tuple[list[float], int]] = []
    for index, example in enumerate(examples):
        if example.role not in _ROLE_INDEX:
            raise ValueError(f"unsupported semantic role: {example.role}")
        node = LayoutNode(
            box_id=f"semantic-{index}",
            text=example.text,
            confidence=1.0,
            polygon=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
            role=example.role,
            group=None,
        )
        encoded.append((node_features(node, width=1.0, height=1.0), _ROLE_INDEX[example.role]))
    return encoded


def _fit_node_classifier(
    examples: list[tuple[list[float], int]],
    *,
    weights: list[list[float]],
    bias: list[float],
    epochs: int,
    rng: random.Random,
    learning_rate: float,
    l2: float,
) -> None:
    class_counts = [0] * len(ROLE_LABELS)
    for _, label in examples:
        class_counts[label] += 1
    class_weights = [
        len(examples) / (len(ROLE_LABELS) * count) if count else 0.0
        for count in class_counts
    ]
    for epoch in range(epochs):
        rng.shuffle(examples)
        rate = learning_rate / math.sqrt(epoch + 1.0)
        for features, label in examples:
            scores = [
                _dot(class_weights_row, features) + class_bias
                for class_weights_row, class_bias in zip(weights, bias, strict=True)
            ]
            probs = _softmax(scores)
            sample_weight = min(class_weights[label], 4.0)
            for class_index in range(len(ROLE_LABELS)):
                delta = (probs[class_index] - (1.0 if class_index == label else 0.0)) * sample_weight
                row = weights[class_index]
                for feature_index, feature in enumerate(features):
                    row[feature_index] -= rate * (delta * feature + l2 * row[feature_index])
                bias[class_index] -= rate * delta


def pretrain_node_model(
    examples: Sequence[SemanticExample],
    *,
    epochs: int = 12,
    seed: int = 112,
    learning_rate: float = 0.12,
    l2: float = 1e-4,
) -> dict[str, object]:
    if not examples:
        raise ValueError("semantic pretraining requires examples")
    encoded = _semantic_node_examples(examples)
    weights = [[0.0] * NODE_FEATURE_DIM for _ in ROLE_LABELS]
    bias = [0.0] * len(ROLE_LABELS)
    _fit_node_classifier(
        encoded,
        weights=weights,
        bias=bias,
        epochs=epochs,
        rng=random.Random(seed),
        learning_rate=learning_rate,
        l2=l2,
    )
    counts = {role: 0 for role in ROLE_LABELS}
    for example in examples:
        counts[example.role] += 1
    return {
        "model_id": "semantic_role_pretrain_v1",
        "roles": list(ROLE_LABELS),
        "node_feature_dim": NODE_FEATURE_DIM,
        "node_weights": weights,
        "node_bias": bias,
        "training": {"examples": len(examples), "role_counts": counts, "epochs": epochs, "seed": seed},
    }


def _edge_examples(documents: Sequence[LabeledDocument]) -> list[tuple[list[float], int]]:
    examples: list[tuple[list[float], int]] = []
    for document in documents:
        products = [node for node in document.nodes if node.role == "product" and node.group]
        fields = [node for node in document.nodes if node.role in {"dose", "frequency", "duration"} and node.group]
        for field in fields:
            for product in products:
                examples.append(
                    (
                        _edge_features(
                            product,
                            field,
                            width=document.width,
                            height=document.height,
                            field_role=str(field.role),
                            candidate_products=products,
                        ),
                        1 if product.group == field.group else 0,
                    )
                )
    return examples


def train_model(
    documents: Sequence[LabeledDocument],
    *,
    epochs: int = 60,
    seed: int = 112,
    learning_rate: float = 0.12,
    l2: float = 1e-4,
    node_initializer: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if not documents:
        raise ValueError("at least one labeled document is required")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    rng = random.Random(seed)
    node_examples = _node_examples(documents)
    edge_examples = _edge_examples(documents)
    if not edge_examples or not any(label for _, label in edge_examples):
        raise ValueError("training documents must contain positive medication edges")

    if node_initializer is None:
        node_weights = [[0.0] * NODE_FEATURE_DIM for _ in ROLE_LABELS]
        node_bias = [0.0] * len(ROLE_LABELS)
        initializer_id = None
    else:
        if node_initializer.get("roles") != list(ROLE_LABELS) or node_initializer.get("node_feature_dim") != NODE_FEATURE_DIM:
            raise ValueError("node initializer feature contract mismatch")
        raw_weights = node_initializer.get("node_weights")
        raw_bias = node_initializer.get("node_bias")
        if not isinstance(raw_weights, list) or not isinstance(raw_bias, list):
            raise ValueError("node initializer weights are missing")
        node_weights = [[float(value) for value in row] for row in raw_weights]
        node_bias = [float(value) for value in raw_bias]
        initializer_id = str(node_initializer.get("model_id") or "unknown")

    _fit_node_classifier(
        node_examples,
        weights=node_weights,
        bias=node_bias,
        epochs=epochs,
        rng=rng,
        learning_rate=learning_rate,
        l2=l2,
    )

    edge_dim = len(edge_examples[0][0])
    edge_weights = [0.0] * edge_dim
    edge_bias = 0.0
    positive_count = sum(label for _, label in edge_examples)
    negative_count = len(edge_examples) - positive_count
    positive_weight = len(edge_examples) / (2.0 * positive_count)
    negative_weight = len(edge_examples) / (2.0 * max(negative_count, 1))
    for epoch in range(epochs):
        rng.shuffle(edge_examples)
        rate = learning_rate / math.sqrt(epoch + 1.0)
        for features, label in edge_examples:
            probability = _sigmoid(_dot(edge_weights, features) + edge_bias)
            sample_weight = positive_weight if label else negative_weight
            delta = (probability - label) * sample_weight
            for feature_index, feature in enumerate(features):
                edge_weights[feature_index] -= rate * (delta * feature + l2 * edge_weights[feature_index])
            edge_bias -= rate * delta

    false_critical_confidences: list[float] = []
    for features, label in node_examples:
        scores = [_dot(weights, features) + bias for weights, bias in zip(node_weights, node_bias, strict=True)]
        probabilities = _softmax(scores)
        predicted = max(range(len(ROLE_LABELS)), key=lambda index: probabilities[index])
        if predicted != label and ROLE_LABELS[predicted] != "other":
            false_critical_confidences.append(probabilities[predicted])
    node_threshold = max(
        0.45,
        (max(false_critical_confidences) + 0.02) if false_critical_confidences else 0.45,
    )
    node_threshold = min(node_threshold, 0.98)

    negative_edge_scores = [
        _sigmoid(_dot(edge_weights, features) + edge_bias)
        for features, label in edge_examples
        if not label
    ]
    edge_threshold = max(
        0.60,
        (max(negative_edge_scores) + 0.02) if negative_edge_scores else 0.60,
    )
    edge_threshold = min(edge_threshold, 0.98)

    return {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "roles": list(ROLE_LABELS),
        "node_feature_dim": NODE_FEATURE_DIM,
        "node_weights": node_weights,
        "node_bias": node_bias,
        "edge_feature_dim": edge_dim,
        "edge_weights": edge_weights,
        "edge_bias": edge_bias,
        "thresholds": {
            "node": node_threshold,
            "edge": edge_threshold,
            "edge_margin": 0.05,
        },
        "calibration": {
            "false_critical_node_max": max(false_critical_confidences) if false_critical_confidences else 0.0,
            "negative_edge_max": max(negative_edge_scores) if negative_edge_scores else 0.0,
            "policy": "training_false_positive_ceiling_plus_0.02",
        },
        "training": {
            "documents": len(documents),
            "node_examples": len(node_examples),
            "edge_examples": len(edge_examples),
            "epochs": epochs,
            "seed": seed,
            "node_initializer": initializer_id,
        },
    }


def save_model(model: Mapping[str, object], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    temporary.write_text(json.dumps(dict(model), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def load_model(path: str | Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("model_id") != MODEL_ID:
        raise ValueError("unsupported learned layout model")
    if value.get("roles") != list(ROLE_LABELS) or value.get("node_feature_dim") != NODE_FEATURE_DIM:
        raise ValueError("learned layout model feature contract mismatch")
    return value


def node_role_scores(model: Mapping[str, object], document: LabeledDocument) -> dict[str, dict[str, float]]:
    weights = model["node_weights"]
    bias = model["node_bias"]
    if not isinstance(weights, list) or not isinstance(bias, list):
        raise ValueError("invalid node model weights")
    result: dict[str, dict[str, float]] = {}
    for node in document.nodes:
        features = node_features(node, width=document.width, height=document.height)
        scores = [
            _dot(class_weights, features) + float(class_bias)
            for class_weights, class_bias in zip(weights, bias, strict=True)
        ]
        probs = _softmax(scores)
        result[node.box_id] = {role: probs[index] for index, role in enumerate(ROLE_LABELS)}
    return result


def edge_score(
    model: Mapping[str, object],
    document: LabeledDocument,
    product: LayoutNode,
    field: LayoutNode,
    field_role: str,
    candidate_products: Sequence[LayoutNode] | None = None,
) -> float:
    products = list(candidate_products) if candidate_products is not None else [
        node for node in document.nodes if node.role == "product"
    ]
    if not products:
        raise ValueError("edge scoring requires at least one product candidate")
    features = _edge_features(
        product,
        field,
        width=document.width,
        height=document.height,
        field_role=field_role,
        candidate_products=products,
    )
    weights = model["edge_weights"]
    if not isinstance(weights, list):
        raise ValueError("invalid edge model weights")
    return _sigmoid(_dot(weights, features) + float(model["edge_bias"]))


def _unit(raw: str) -> str:
    lowered = raw.lower()
    if lowered in {"정", "tablet"}:
        return "tablet"
    if lowered in {"캡슐", "capsule"}:
        return "capsule"
    if lowered == "포":
        return "packet"
    if lowered == "ml":
        return "mL"
    return raw


def _field_values(role: str, text: str) -> dict[str, object]:
    compact = _compact(text)
    if role == "dose":
        packet_tablet = _PACKET_TABLET.fullmatch(compact)
        if packet_tablet:
            amount = float(packet_tablet.group(1))
            return {
                "dose_amount": int(amount) if amount.is_integer() else amount,
                "dosage_text": compact,
            }
        match = _DOSE_VALUE.fullmatch(compact)
        if match:
            amount = float(match.group(1))
            return {
                "dose_amount": int(amount) if amount.is_integer() else amount,
                "dose_unit": _unit(match.group(2)),
            }
    if role == "frequency" and (match := _FREQUENCY_VALUE.fullmatch(compact)):
        return {"frequency_per_day": int(match.group(1))}
    if role == "duration" and (match := _DURATION_VALUE.fullmatch(compact)):
        return {"prescription_days": int(match.group(1))}
    return {}


def _clean_product(text: str) -> str:
    value = _compact(text)
    return re.sub(r"^(?:약명|제품명|약품명|의약품명)[:：]?", "", value).strip()


def assemble_rows(
    *,
    products: Sequence[LayoutNode],
    fields: Sequence[tuple[str, LayoutNode]],
    edge_scores: Mapping[tuple[str, str], float],
    edge_threshold: float,
    edge_margin: float,
) -> list[dict[str, object]]:
    ordered_products = sorted(products, key=lambda node: (_center(node)[1], _center(node)[0], node.box_id))
    rows: list[dict[str, object]] = []
    row_by_product: dict[str, dict[str, object]] = {}
    for product in ordered_products:
        product_query = _clean_product(product.text)
        if not product_query:
            continue
        row = {
            "row_id": product.box_id,
            "product_query": product_query,
            "draft": {},
            "uncertainty_codes": [],
            "evidence": {"product_query": [product.box_id]},
        }
        rows.append(row)
        row_by_product[product.box_id] = row

    selected: dict[tuple[str, str], tuple[float, LayoutNode]] = {}
    for role, field in fields:
        ranked = sorted(
            ((float(edge_scores.get((product.box_id, field.box_id), 0.0)), product) for product in ordered_products),
            key=lambda item: (item[0], item[1].box_id),
            reverse=True,
        )
        if not ranked:
            continue
        best_score, best_product = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_score < edge_threshold or best_score - second_score < edge_margin:
            continue
        key = (best_product.box_id, role)
        previous = selected.get(key)
        if previous is None or best_score > previous[0]:
            selected[key] = (best_score, field)

    for (product_id, role), (_, field) in selected.items():
        row = row_by_product.get(product_id)
        if row is None:
            continue
        values = _field_values(role, field.text)
        if not values:
            continue
        draft = row["draft"]
        evidence = row["evidence"]
        assert isinstance(draft, dict) and isinstance(evidence, dict)
        for name, value in values.items():
            draft[name] = value
            evidence[name] = [field.box_id]
    return rows


def predict_rows(model: Mapping[str, object], document: LabeledDocument) -> list[dict[str, object]]:
    probabilities = node_role_scores(model, document)
    thresholds = model.get("thresholds")
    if not isinstance(thresholds, Mapping):
        raise ValueError("model thresholds are missing")
    node_threshold = float(thresholds["node"])
    products: list[LayoutNode] = []
    fields: list[tuple[str, LayoutNode]] = []
    predicted_role: dict[str, str] = {}
    for node in document.nodes:
        scores = probabilities[node.box_id]
        role, score = max(scores.items(), key=lambda item: (item[1], item[0]))
        if role == "other" or score < node_threshold:
            continue
        predicted_role[node.box_id] = role
        if role == "product":
            products.append(node)
        elif role in {"dose", "frequency", "duration"}:
            fields.append((role, node))

    edge_scores: dict[tuple[str, str], float] = {}
    for role, field in fields:
        for product in products:
            edge_scores[(product.box_id, field.box_id)] = edge_score(
                model,
                document,
                product,
                field,
                role,
                candidate_products=products,
            )
    return assemble_rows(
        products=products,
        fields=fields,
        edge_scores=edge_scores,
        edge_threshold=float(thresholds["edge"]),
        edge_margin=float(thresholds["edge_margin"]),
    )


__all__ = [
    "LayoutNode",
    "LabeledDocument",
    "MODEL_ID",
    "NODE_FEATURE_DIM",
    "ROLE_LABELS",
    "SemanticExample",
    "assemble_rows",
    "edge_score",
    "load_model",
    "node_features",
    "node_role_scores",
    "predict_rows",
    "pretrain_node_model",
    "save_model",
    "train_model",
]