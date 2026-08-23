from __future__ import annotations

import json
import unittest
from pathlib import Path

from browser_ocr.document_parsing.document_graph import (
    PAGE_NODE_ID,
    GraphEncoderSpec,
    build_document_graph,
    graph_encoder_parameter_count,
    relation_pair_features,
)


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _node(
    node_id: str,
    text: str,
    x: float,
    y: float,
    *,
    role: str | None = "other",
    group: str | None = None,
    status: str = "labeled",
    confidence: float = 0.95,
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "text": text,
        "confidence": confidence,
        "polygon": _poly(x, y),
        "target_region_ids": [],
        "label_status": status,
        "semantic_role": role,
        "association_group": group,
    }


def _document(nodes: list[dict[str, object]], relations: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "document_id": "graph-fixture",
        "split": "train",
        "source_kind": "synthetic",
        "image_sha256": "a" * 64,
        "width": 1000,
        "height": 1400,
        "layout_family": "fixture",
        "scenario_tags": [],
        "risk_tags": [],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "provenance": {"source_id": "fixture", "license_id": "procedural-synthetic"},
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": "b" * 64},
        "observation": {"kind": "runtime_ocr", "profile": {}, "nodes": nodes},
        "relations": relations or [],
        "gold_rows": [],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


class DocumentGraphTest(unittest.TestCase):
    def test_shared_js_graph_contract_fixture_matches_python_features(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "parser_graph_contract.json").read_text(encoding="utf-8")
        )
        nodes = [
            {
                "node_id": item["id"],
                "text": item["text"],
                "confidence": item["score"],
                "polygon": item["poly"],
                "target_region_ids": [],
                "label_status": "unlabeled",
                "semantic_role": None,
                "association_group": None,
            }
            for item in fixture["items"]
        ]
        document = _document(nodes)
        document["width"] = fixture["width"]
        document["height"] = fixture["height"]
        graph = build_document_graph(document, neighbor_count=fixture["neighbor_count"])

        self.assertEqual([node.node_id for node in graph.nodes], fixture["node_ids"])
        for actual, expected in zip(
            [value for node in graph.nodes for value in node.features],
            [value for node in fixture["node_features"] for value in node],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertEqual([[edge.source, edge.target] for edge in graph.edges], fixture["edge_index"])
        for actual, expected in zip(
            [value for edge in graph.edges for value in edge.features],
            [value for edge in fixture["edge_features"] for value in edge],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected, places=12)
        for pair in fixture["relation_pairs"]:
            product_index = graph.node_index[pair["ids"][0]]
            field_index = graph.node_index[pair["ids"][1]]
            for actual, expected in zip(
                relation_pair_features(graph, product_index, field_index),
                pair["features"],
                strict=True,
            ):
                self.assertAlmostEqual(actual, expected, places=12)

    def test_sparse_graph_is_bounded_and_every_ocr_node_has_page_context(self) -> None:
        nodes = [_node(f"n{index}", f"텍스트{index}", (index % 5) * 150, (index // 5) * 80) for index in range(20)]
        graph = build_document_graph(_document(nodes), neighbor_count=4)

        self.assertEqual(graph.nodes[0].node_id, PAGE_NODE_ID)
        self.assertEqual(len(graph.nodes), 21)
        local_edges = [edge for edge in graph.edges if edge.kind == "spatial"]
        page_edges = [edge for edge in graph.edges if edge.kind == "page"]
        self.assertLessEqual(len(local_edges), 20 * 4)
        self.assertEqual(len(page_edges), 40)
        self.assertEqual(
            {edge.target for edge in page_edges if edge.source == 0},
            set(range(1, 21)),
        )
        self.assertEqual(
            {edge.source for edge in page_edges if edge.target == 0},
            set(range(1, 21)),
        )

    def test_same_target_text_uses_different_context_when_neighbors_change(self) -> None:
        common = _node("target", "30", 500, 500, role="duration", group="m1")
        medication_context = [
            _node("product", "가나다정", 120, 500, role="product", group="m1"),
            common,
            _node("dose", "1정", 360, 500, role="dose", group="m1"),
            _node("freq", "3회", 440, 500, role="frequency", group="m1"),
        ]
        receipt_context = [
            _node("receipt-label", "투약일수", 360, 500),
            common,
            _node("amount", "17,100", 500, 560),
            _node("copay", "4,500", 500, 620),
        ]
        medication = build_document_graph(_document(medication_context), neighbor_count=3)
        receipt = build_document_graph(_document(receipt_context), neighbor_count=3)
        med_target = medication.node_index["target"]
        receipt_target = receipt.node_index["target"]

        self.assertEqual(medication.nodes[med_target].features, receipt.nodes[receipt_target].features)
        med_neighbors = {medication.nodes[edge.source].text for edge in medication.edges if edge.kind == "spatial" and edge.target == med_target}
        receipt_neighbors = {receipt.nodes[edge.source].text for edge in receipt.edges if edge.kind == "spatial" and edge.target == receipt_target}
        self.assertNotEqual(med_neighbors, receipt_neighbors)
        self.assertIn("가나다정", med_neighbors)
        self.assertIn("17,100", receipt_neighbors)

    def test_ambiguous_nodes_remain_context_but_are_masked_from_targets(self) -> None:
        graph = build_document_graph(_document([
            _node("product", "가나다정", 100, 100, role="product", group="m1"),
            _node("merged", "1정3회", 300, 100, role=None, group=None, status="ambiguous"),
            _node("receipt", "4,500", 700, 700, role="other"),
        ]), neighbor_count=2)

        merged = graph.nodes[graph.node_index["merged"]]
        receipt = graph.nodes[graph.node_index["receipt"]]
        self.assertFalse(merged.supervised)
        self.assertIsNone(merged.role_target)
        self.assertTrue(receipt.supervised)
        self.assertEqual(receipt.role_target, graph.role_labels.index("other"))
        self.assertTrue(any(edge.source == graph.node_index["merged"] for edge in graph.edges))

    def test_relation_targets_preserve_same_and_different_medication_supervision(self) -> None:
        nodes = [
            _node("p1", "가나다정", 100, 100, role="product", group="m1"),
            _node("p2", "라마바정", 100, 300, role="product", group="m2"),
            _node("d1", "1정", 400, 100, role="dose", group="m1"),
        ]
        relations = [
            {"product_node_id": "p1", "field_node_id": "d1", "label": "same_medication"},
            {"product_node_id": "p2", "field_node_id": "d1", "label": "different_medication"},
        ]
        graph = build_document_graph(_document(nodes, relations), neighbor_count=2)
        targets = {(graph.nodes[target.product_index].node_id, graph.nodes[target.field_index].node_id): target.label for target in graph.relations}
        self.assertEqual(targets[("p1", "d1")], 1)
        self.assertEqual(targets[("p2", "d1")], 0)

    def test_initial_mobile_graph_encoder_budget_is_well_below_one_million_parameters(self) -> None:
        spec = GraphEncoderSpec(hidden_dim=96, layers=2, neighbor_count=12, pair_hidden_dim=64)
        count = graph_encoder_parameter_count(spec)
        self.assertGreater(count, 50_000)
        self.assertLess(count, 1_000_000)
        self.assertEqual(spec.neighbor_count, 12)


if __name__ == "__main__":
    unittest.main()