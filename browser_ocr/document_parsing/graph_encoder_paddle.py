from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .document_graph import (
    EDGE_FEATURE_DIM,
    NODE_FEATURE_DIM,
    ROLE_LABELS,
    DocumentGraph,
    GraphEncoderSpec,
)


@dataclass(frozen=True)
class GraphTensors:
    node_features: paddle.Tensor
    edge_index: paddle.Tensor
    edge_features: paddle.Tensor
    role_index: paddle.Tensor
    role_targets: paddle.Tensor
    relation_index: paddle.Tensor
    relation_features: paddle.Tensor
    relation_labels: paddle.Tensor
    role_labels: tuple[str, ...]


def graph_tensors(graph: DocumentGraph) -> GraphTensors:
    role_rows = [
        (index, node.role_target)
        for index, node in enumerate(graph.nodes)
        if node.supervised and node.role_target is not None
    ]
    return GraphTensors(
        node_features=paddle.to_tensor([list(node.features) for node in graph.nodes], dtype="float32"),
        edge_index=paddle.to_tensor([[edge.source, edge.target] for edge in graph.edges], dtype="int64").reshape([-1, 2]),
        edge_features=paddle.to_tensor([list(edge.features) for edge in graph.edges], dtype="float32").reshape([-1, EDGE_FEATURE_DIM]),
        role_index=paddle.to_tensor([index for index, _ in role_rows], dtype="int64"),
        role_targets=paddle.to_tensor([target for _, target in role_rows], dtype="int64"),
        relation_index=paddle.to_tensor(
            [[relation.product_index, relation.field_index] for relation in graph.relations], dtype="int64"
        ).reshape([-1, 2]),
        relation_features=paddle.to_tensor(
            [list(relation.features) for relation in graph.relations], dtype="float32"
        ).reshape([-1, EDGE_FEATURE_DIM]),
        relation_labels=paddle.to_tensor([float(relation.label) for relation in graph.relations], dtype="float32"),
        role_labels=graph.role_labels,
    )


class _SparseMessageLayer(nn.Layer):
    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.self_projection = nn.Linear(hidden_dim, hidden_dim)
        self.neighbor_projection = nn.Linear(hidden_dim, hidden_dim)
        self.edge_projection = nn.Linear(EDGE_FEATURE_DIM, hidden_dim)

    def forward(
        self,
        hidden: paddle.Tensor,
        edge_index: paddle.Tensor,
        edge_features: paddle.Tensor,
    ) -> paddle.Tensor:
        if edge_index.shape[0] == 0:
            aggregate = paddle.zeros_like(hidden)
        else:
            source_index = edge_index[:, 0]
            target_index = edge_index[:, 1]
            source_hidden = paddle.gather(hidden, source_index, axis=0)
            messages = self.neighbor_projection(source_hidden) + self.edge_projection(edge_features)
            aggregate = paddle.scatter_nd_add(
                paddle.zeros_like(hidden),
                target_index.reshape([-1, 1]),
                messages,
            )
            degree = paddle.scatter_nd_add(
                paddle.zeros([hidden.shape[0], 1], dtype=hidden.dtype),
                target_index.reshape([-1, 1]),
                paddle.ones([target_index.shape[0], 1], dtype=hidden.dtype),
            )
            aggregate = aggregate / paddle.clip(degree, min=1.0)
        return F.gelu(self.self_projection(hidden) + aggregate)


class SparseDocumentGraphEncoder(nn.Layer):
    """Small full-document message-passing encoder for OCR graphs.

    The page token and sparse spatial edges are supplied by `document_graph`.
    This module deliberately has no template/layout-family inputs and no image
    branch; original-page visual features are a separate research axis.
    """

    def __init__(self, spec: GraphEncoderSpec = GraphEncoderSpec()) -> None:
        super().__init__()
        self.spec = spec
        self.input_projection = nn.Linear(NODE_FEATURE_DIM, spec.hidden_dim)
        self.layers = nn.LayerList([_SparseMessageLayer(spec.hidden_dim) for _ in range(spec.layers)])
        self.role_head = nn.Linear(spec.hidden_dim, len(ROLE_LABELS))
        self.pair_hidden = nn.Linear(spec.hidden_dim * 2 + EDGE_FEATURE_DIM, spec.pair_hidden_dim)
        self.pair_output = nn.Linear(spec.pair_hidden_dim, 1)

    def encode(
        self,
        node_features: paddle.Tensor,
        edge_index: paddle.Tensor,
        edge_features: paddle.Tensor,
    ) -> paddle.Tensor:
        hidden = F.gelu(self.input_projection(node_features))
        for layer in self.layers:
            hidden = layer(hidden, edge_index, edge_features)
        return hidden

    def forward(
        self,
        node_features: paddle.Tensor,
        edge_index: paddle.Tensor,
        edge_features: paddle.Tensor,
        relation_index: paddle.Tensor,
        relation_features: paddle.Tensor,
    ) -> tuple[paddle.Tensor, paddle.Tensor]:
        hidden = self.encode(node_features, edge_index, edge_features)
        role_logits = self.role_head(hidden)
        return role_logits, self.relation_logits(hidden, relation_index, relation_features)

    def relation_logits(
        self,
        hidden: paddle.Tensor,
        relation_index: paddle.Tensor,
        relation_features: paddle.Tensor,
    ) -> paddle.Tensor:
        if relation_index.shape[0] == 0:
            return paddle.zeros([0], dtype=hidden.dtype)
        product_hidden = paddle.gather(hidden, relation_index[:, 0], axis=0)
        field_hidden = paddle.gather(hidden, relation_index[:, 1], axis=0)
        pair_input = paddle.concat([product_hidden, field_hidden, relation_features], axis=1)
        pair_hidden = F.gelu(self.pair_hidden(pair_input))
        return self.pair_output(pair_hidden).reshape([-1])


def graph_loss(
    role_logits: paddle.Tensor,
    relation_logits: paddle.Tensor,
    tensors: GraphTensors,
    *,
    role_weight: paddle.Tensor | None = None,
    relation_loss_weight: float = 1.0,
    relation_pos_weight: float = 1.0,
) -> paddle.Tensor:
    losses: list[paddle.Tensor] = []
    if tensors.role_index.shape[0] > 0:
        supervised_logits = paddle.gather(role_logits, tensors.role_index, axis=0)
        losses.append(F.cross_entropy(supervised_logits, tensors.role_targets, weight=role_weight))
    if tensors.relation_labels.shape[0] > 0:
        positive = tensors.relation_labels * float(relation_pos_weight) * F.softplus(-relation_logits)
        negative = (1.0 - tensors.relation_labels) * F.softplus(relation_logits)
        relation_loss = paddle.mean(positive + negative)
        losses.append(relation_loss * float(relation_loss_weight))
    if not losses:
        raise ValueError("graph has no supervised role or relation targets")
    total = losses[0]
    for loss in losses[1:]:
        total = total + loss
    return total


def model_parameter_count(model: nn.Layer) -> int:
    return sum(int(parameter.numel()) for parameter in model.parameters())


def architecture_manifest(spec: GraphEncoderSpec) -> dict[str, Any]:
    return {
        "model_id": "sparse_document_graph_v1",
        "node_feature_dim": NODE_FEATURE_DIM,
        "edge_feature_dim": EDGE_FEATURE_DIM,
        "role_labels": list(ROLE_LABELS),
        "hidden_dim": spec.hidden_dim,
        "layers": spec.layers,
        "neighbor_count": spec.neighbor_count,
        "pair_hidden_dim": spec.pair_hidden_dim,
    }


__all__ = [
    "GraphTensors",
    "SparseDocumentGraphEncoder",
    "architecture_manifest",
    "graph_loss",
    "graph_tensors",
    "model_parameter_count",
]