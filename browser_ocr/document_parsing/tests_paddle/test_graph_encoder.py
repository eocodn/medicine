from __future__ import annotations

import unittest

import paddle

from browser_ocr.document_parsing.document_graph import (
    EDGE_FEATURE_DIM,
    GraphEncoderSpec,
    build_document_graph,
    graph_encoder_parameter_count,
)
from browser_ocr.document_parsing.graph_encoder_paddle import (
    SparseDocumentGraphEncoder,
    graph_loss,
    graph_tensors,
)


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _node(node_id: str, text: str, x: float, y: float, *, role: str | None, group: str | None = None, status: str = "labeled") -> dict[str, object]:
    return {
        "node_id": node_id,
        "text": text,
        "confidence": 0.98,
        "polygon": _poly(x, y),
        "target_region_ids": [],
        "label_status": status,
        "semantic_role": role,
        "association_group": group,
    }


def _document(document_id: str, nodes: list[dict[str, object]], relations: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "document_id": document_id,
        "width": 1000,
        "height": 1400,
        "observation": {"nodes": nodes},
        "relations": relations or [],
    }


class GraphEncoderPaddleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_actual_parameter_count_matches_mobile_budget_contract(self) -> None:
        spec = GraphEncoderSpec()
        model = SparseDocumentGraphEncoder(spec)
        actual = sum(int(parameter.numel()) for parameter in model.parameters())
        self.assertEqual(actual, graph_encoder_parameter_count(spec))
        self.assertLess(actual, 1_000_000)

    def test_forward_and_loss_cover_role_and_association_heads(self) -> None:
        graph = build_document_graph(_document("association", [
            _node("p1", "가나다정", 100, 100, role="product", group="m1"),
            _node("p2", "라마바정", 100, 300, role="product", group="m2"),
            _node("d1", "1정", 400, 100, role="dose", group="m1"),
        ], [
            {"product_node_id": "p1", "field_node_id": "d1", "label": "same_medication"},
            {"product_node_id": "p2", "field_node_id": "d1", "label": "different_medication"},
        ]), neighbor_count=2)
        tensors = graph_tensors(graph)
        model = SparseDocumentGraphEncoder(GraphEncoderSpec(hidden_dim=32, layers=2, neighbor_count=2, pair_hidden_dim=24))
        role_logits, relation_logits = model(
            tensors.node_features,
            tensors.edge_index,
            tensors.edge_features,
            tensors.relation_index,
            tensors.relation_features,
        )
        self.assertEqual(tuple(role_logits.shape), (4, len(graph.role_labels)))
        self.assertEqual(tuple(relation_logits.shape), (2,))
        self.assertEqual(tuple(tensors.edge_features.shape)[1], EDGE_FEATURE_DIM)
        loss = graph_loss(role_logits, relation_logits, tensors)
        self.assertTrue(bool(paddle.isfinite(loss).item()))
        loss.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

    def test_message_passing_learns_contextual_role_for_identical_target_input(self) -> None:
        paddle.seed(41)
        medication_nodes = [
            _node("product", "가나다정", 250, 500, role=None, status="ambiguous"),
            _node("dose", "1정", 400, 500, role=None, status="ambiguous"),
            _node("target", "30", 500, 500, role="duration", group="m1"),
        ]
        receipt_nodes = [
            _node("receipt-label", "투약일수", 250, 500, role=None, status="ambiguous"),
            _node("amount", "17,100", 400, 500, role=None, status="ambiguous"),
            _node("target", "30", 500, 500, role="other"),
        ]
        medication = graph_tensors(build_document_graph(_document("medication", medication_nodes), neighbor_count=2))
        receipt = graph_tensors(build_document_graph(_document("receipt", receipt_nodes), neighbor_count=2))
        self.assertTrue(bool(paddle.allclose(medication.node_features[3], receipt.node_features[3]).item()))

        model = SparseDocumentGraphEncoder(GraphEncoderSpec(hidden_dim=32, layers=2, neighbor_count=2, pair_hidden_dim=24))
        optimizer = paddle.optimizer.Adam(learning_rate=0.02, parameters=model.parameters())
        for _ in range(120):
            total = paddle.zeros([], dtype="float32")
            for tensors in (medication, receipt):
                role_logits, relation_logits = model(
                    tensors.node_features,
                    tensors.edge_index,
                    tensors.edge_features,
                    tensors.relation_index,
                    tensors.relation_features,
                )
                total = total + graph_loss(role_logits, relation_logits, tensors)
            total.backward()
            optimizer.step()
            optimizer.clear_grad()

        def target_role(tensors) -> int:
            role_logits, _ = model(
                tensors.node_features,
                tensors.edge_index,
                tensors.edge_features,
                tensors.relation_index,
                tensors.relation_features,
            )
            return int(paddle.argmax(role_logits[3]).item())

        self.assertEqual(target_role(medication), medication.role_labels.index("duration"))
        self.assertEqual(target_role(receipt), receipt.role_labels.index("other"))


if __name__ == "__main__":
    unittest.main()