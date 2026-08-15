from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.learned_layout import (
    LayoutNode,
    LabeledDocument,
    SemanticExample,
    assemble_rows,
    load_model,
    node_features,
    node_role_scores,
    predict_rows,
    pretrain_node_model,
    save_model,
    train_model,
)


def _node(
    box_id: str,
    text: str,
    x: float,
    y: float,
    role: str,
    group: str | None,
) -> LayoutNode:
    return LayoutNode(
        box_id=box_id,
        text=text,
        confidence=0.99,
        polygon=((x, y), (x + 80, y), (x + 80, y + 24), (x, y + 24)),
        role=role,
        group=group,
    )


def _document(index: int) -> LabeledDocument:
    dy = float(index % 3)
    nodes = (
        _node(f"h-{index}", "약품명", 10, 5 + dy, "other", None),
        _node(f"p1-{index}", "가나다정", 10, 50 + dy, "product", "med-a"),
        _node(f"d1-{index}", "1정", 220, 50 + dy, "dose", "med-a"),
        _node(f"f1-{index}", "2회", 360, 50 + dy, "frequency", "med-a"),
        _node(f"t1-{index}", "5일", 500, 50 + dy, "duration", "med-a"),
        _node(f"p2-{index}", "라마바정", 10, 110 + dy, "product", "med-b"),
        _node(f"d2-{index}", "2정", 220, 110 + dy, "dose", "med-b"),
        _node(f"f2-{index}", "3회", 360, 110 + dy, "frequency", "med-b"),
        _node(f"t2-{index}", "7일", 500, 110 + dy, "duration", "med-b"),
    )
    return LabeledDocument(
        sample_id=f"sample-{index}",
        width=640,
        height=200,
        nodes=nodes,
        layout_family="table",
        capture_profile="clean",
    )


def _same_text_context_document(index: int, *, label_context: bool) -> LabeledDocument:
    dy = float(index % 3)
    target_role = "other" if label_context else "duration"
    target_group = None if label_context else "med-a"
    if label_context:
        nodes = (
            _node(f"target-{index}", "1일", 400, 50 + dy, target_role, target_group),
            _node(f"freq-{index}", "3회", 500, 50 + dy, "frequency", "med-a"),
            _node(f"dose-{index}", "1정", 500, 90 + dy, "dose", "med-a"),
            _node(f"days-{index}", "5일", 500, 130 + dy, "duration", "med-a"),
            _node(f"product-{index}", "가나다정", 250, 170 + dy, "product", "med-a"),
        )
    else:
        nodes = (
            _node(f"product-{index}", "가나다정", 10, 50 + dy, "product", "med-a"),
            _node(f"dose-{index}", "1정", 180, 50 + dy, "dose", "med-a"),
            _node(f"freq-{index}", "3회", 290, 50 + dy, "frequency", "med-a"),
            _node(f"target-{index}", "1일", 400, 50 + dy, target_role, target_group),
        )
    return LabeledDocument(
        sample_id=f"context-{index}-{'label' if label_context else 'value'}",
        width=640,
        height=220,
        nodes=nodes,
        layout_family="contextual",
        capture_profile="clean",
    )


class LearnedLayoutTest(unittest.TestCase):
    def test_node_features_are_fixed_size_and_deterministic(self) -> None:
        node = _node("dose", "0.5정", 100, 50, "dose", "med-a")
        first = node_features(node, width=640, height=200)
        second = node_features(node, width=640, height=200)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertTrue(all(isinstance(value, float) for value in first))

    def test_tiny_model_learns_roles_and_same_medication_edges(self) -> None:
        model = train_model([_document(index) for index in range(24)], epochs=80, seed=17)
        heldout = _document(101)
        rows = predict_rows(model, heldout)

        self.assertEqual([row["product_query"] for row in rows], ["가나다정", "라마바정"])
        self.assertEqual(rows[0]["draft"]["dose_amount"], 1)
        self.assertEqual(rows[0]["draft"]["frequency_per_day"], 2)
        self.assertEqual(rows[0]["draft"]["prescription_days"], 5)
        self.assertEqual(rows[1]["draft"]["dose_amount"], 2)
        self.assertEqual(rows[1]["draft"]["frequency_per_day"], 3)
        self.assertEqual(rows[1]["draft"]["prescription_days"], 7)
        self.assertIn("p1-101", rows[0]["evidence"]["product_query"])
        self.assertIn("d1-101", rows[0]["evidence"]["dose_amount"])

    def test_context_head_assigns_same_text_differently_from_neighbor_structure(self) -> None:
        training = []
        for index in range(20):
            training.append(_same_text_context_document(index, label_context=False))
            training.append(_same_text_context_document(index + 100, label_context=True))
        model = train_model(training, epochs=100, seed=41)

        value_doc = _same_text_context_document(1001, label_context=False)
        label_doc = _same_text_context_document(1002, label_context=True)
        value_scores = node_role_scores(model, value_doc)["target-1001"]
        label_scores = node_role_scores(model, label_doc)["target-1002"]

        self.assertEqual(max(value_scores, key=value_scores.get), "duration")
        self.assertEqual(max(label_scores, key=label_scores.get), "other")
        self.assertGreater(value_scores["duration"], 0.8)
        self.assertGreater(label_scores["other"], 0.8)

    def test_relation_head_generalizes_from_rows_to_repeated_vertical_blocks(self) -> None:
        model = train_model([_document(index) for index in range(24)], epochs=80, seed=29)
        nodes = (
            _node("p1", "가나다정", 100, 20, "product", "a"),
            _node("d1", "1정", 100, 55, "dose", "a"),
            _node("f1", "2회", 200, 55, "frequency", "a"),
            _node("t1", "5일", 300, 55, "duration", "a"),
            _node("p2", "라마바정", 100, 120, "product", "b"),
            _node("d2", "2정", 100, 155, "dose", "b"),
            _node("f2", "3회", 200, 155, "frequency", "b"),
            _node("t2", "7일", 300, 155, "duration", "b"),
        )
        heldout = LabeledDocument(
            sample_id="vertical",
            width=640,
            height=220,
            nodes=nodes,
            layout_family="vertical-block",
            capture_profile="clean",
        )
        rows = predict_rows(model, heldout)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["draft"].get("prescription_days"), 5)
        self.assertEqual(rows[1]["draft"].get("prescription_days"), 7)

    def test_model_serialization_round_trip_is_exact(self) -> None:
        model = train_model([_document(index) for index in range(12)], epochs=30, seed=3)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "model.json"
            save_model(model, path)
            reloaded = load_model(path)
            self.assertEqual(reloaded, model)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["model_id"], "hashed_layout_context_v3")

    def test_semantic_pretraining_can_seed_full_document_node_training(self) -> None:
        examples = []
        for _ in range(12):
            examples.extend(
                [
                    SemanticExample("가나다정", "product"),
                    SemanticExample("1정", "dose"),
                    SemanticExample("2회", "frequency"),
                    SemanticExample("5일", "duration"),
                    SemanticExample("주의사항", "other"),
                ]
            )
        initializer = pretrain_node_model(examples, epochs=20, seed=5)
        model = train_model([_document(index) for index in range(8)], epochs=10, seed=6, node_initializer=initializer)
        self.assertEqual(model["training"]["node_initializer"], "semantic_role_pretrain_v1")

    def test_ambiguous_edge_scores_leave_field_unresolved(self) -> None:
        nodes = list(_document(999).nodes)
        products = [node for node in nodes if node.role == "product"]
        dose = next(node for node in nodes if node.box_id.startswith("d1-"))
        rows = assemble_rows(
            products=products,
            fields=[("dose", dose)],
            edge_scores={(products[0].box_id, dose.box_id): 0.82, (products[1].box_id, dose.box_id): 0.80},
            edge_threshold=0.6,
            edge_margin=0.05,
        )
        self.assertEqual(len(rows), 2)
        self.assertNotIn("dose_amount", rows[0]["draft"])
        self.assertNotIn("dose_amount", rows[1]["draft"])


if __name__ == "__main__":
    unittest.main()