from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Mapping, Sequence

from .learned_context import context_feature_dim as _context_feature_dim, contextual_node_features
from .learned_decode import assemble_rows
from .learned_features import (
    NODE_FEATURE_DIM,
    LayoutNode,
    LabeledDocument,
    SemanticExample,
    edge_features as _edge_features,
    node_features,
)


MODEL_ID = "hashed_layout_context_v3"
ROLE_LABELS = ("product", "dose", "frequency", "duration", "other")
_ROLE_INDEX = {label: index for index, label in enumerate(ROLE_LABELS)}
CONTEXT_FEATURE_DIM = _context_feature_dim(len(ROLE_LABELS))


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


def _class_probabilities(
    weights: Sequence[Sequence[float]],
    bias: Sequence[float],
    features: Sequence[float],
) -> list[float]:
    return _softmax(
        [
            _dot(class_weights, features) + float(class_bias)
            for class_weights, class_bias in zip(weights, bias, strict=True)
        ]
    )


def _local_node_role_scores(
    weights: Sequence[Sequence[float]],
    bias: Sequence[float],
    document: LabeledDocument,
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for node in document.nodes:
        features = node_features(node, width=document.width, height=document.height)
        probabilities = _class_probabilities(weights, bias, features)
        result[node.box_id] = {role: probabilities[index] for index, role in enumerate(ROLE_LABELS)}
    return result


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


def _fit_context_classifier(
    examples: list[tuple[list[float], int, list[float]]],
    *,
    weights: list[list[float]],
    bias: list[float],
    epochs: int,
    rng: random.Random,
    learning_rate: float,
    l2: float,
) -> None:
    class_counts = [0] * len(ROLE_LABELS)
    for _, label, _ in examples:
        class_counts[label] += 1
    class_weights = [
        len(examples) / (len(ROLE_LABELS) * count) if count else 0.0
        for count in class_counts
    ]
    for epoch in range(epochs):
        rng.shuffle(examples)
        rate = learning_rate / math.sqrt(epoch + 1.0)
        for features, label, local_probabilities in examples:
            base_logits = [math.log(max(probability, 1e-9)) for probability in local_probabilities]
            scores = [
                base_logits[index] + _dot(row, features) + float(bias[index])
                for index, row in enumerate(weights)
            ]
            probabilities = _softmax(scores)
            sample_weight = min(class_weights[label], 4.0)
            for class_index in range(len(ROLE_LABELS)):
                delta = (probabilities[class_index] - (1.0 if class_index == label else 0.0)) * sample_weight
                row = weights[class_index]
                for feature_index, feature in enumerate(features):
                    row[feature_index] -= rate * (delta * feature + l2 * row[feature_index])
                bias[class_index] -= rate * delta


def _context_probabilities(
    weights: Sequence[Sequence[float]],
    bias: Sequence[float],
    features: Sequence[float],
    local_probabilities: Sequence[float],
) -> list[float]:
    base_logits = [math.log(max(probability, 1e-9)) for probability in local_probabilities]
    return _softmax(
        [
            base_logits[index] + _dot(row, features) + float(bias[index])
            for index, row in enumerate(weights)
        ]
    )


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

    context_examples: list[tuple[list[float], int, list[float]]] = []
    for document in documents:
        local_scores = _local_node_role_scores(node_weights, node_bias, document)
        for node in document.nodes:
            role = node.role if node.role in _ROLE_INDEX else "other"
            context_examples.append(
                (
                    contextual_node_features(document, node, local_scores, ROLE_LABELS),
                    _ROLE_INDEX[role],
                    [local_scores[node.box_id][label] for label in ROLE_LABELS],
                )
            )
    context_weights = [[0.0] * CONTEXT_FEATURE_DIM for _ in ROLE_LABELS]
    context_bias = [0.0] * len(ROLE_LABELS)
    _fit_context_classifier(
        context_examples,
        weights=context_weights,
        bias=context_bias,
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
    for features, label, local_probabilities in context_examples:
        probabilities = _context_probabilities(context_weights, context_bias, features, local_probabilities)
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
        "context_feature_dim": CONTEXT_FEATURE_DIM,
        "context_weights": context_weights,
        "context_bias": context_bias,
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
            "context_examples": len(context_examples),
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
    if (
        value.get("roles") != list(ROLE_LABELS)
        or value.get("node_feature_dim") != NODE_FEATURE_DIM
        or value.get("context_feature_dim") != CONTEXT_FEATURE_DIM
    ):
        raise ValueError("learned layout model feature contract mismatch")
    return value


def node_role_scores(model: Mapping[str, object], document: LabeledDocument) -> dict[str, dict[str, float]]:
    node_weights = model["node_weights"]
    node_bias = model["node_bias"]
    context_weights = model["context_weights"]
    context_bias = model["context_bias"]
    if not all(isinstance(value, list) for value in (node_weights, node_bias, context_weights, context_bias)):
        raise ValueError("invalid contextual node model weights")
    local_scores = _local_node_role_scores(node_weights, node_bias, document)
    result: dict[str, dict[str, float]] = {}
    for node in document.nodes:
        features = contextual_node_features(document, node, local_scores, ROLE_LABELS)
        local_probabilities = [local_scores[node.box_id][role] for role in ROLE_LABELS]
        probabilities = _context_probabilities(context_weights, context_bias, features, local_probabilities)
        result[node.box_id] = {role: probabilities[index] for index, role in enumerate(ROLE_LABELS)}
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