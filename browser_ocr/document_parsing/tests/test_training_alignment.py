from __future__ import annotations

import unittest

from browser_ocr.document_parsing.training_alignment import align_observation_nodes, build_relation_labels


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _gt(region_id: str, role: str, group: str, x: float, y: float, w: float = 80) -> dict:
    return {
        "region_id": region_id,
        "semantic_role": role,
        "association_group": group,
        "polygon": _poly(x, y, w),
        "natural_text_polygon": _poly(x, y, w),
    }


class ParserTrainingAlignmentTest(unittest.TestCase):
    def test_exact_matches_and_detector_false_positive_are_labeled(self) -> None:
        gt = [_gt("p", "product", "m1", 10, 10), _gt("d", "dose", "m1", 120, 10)]
        observed = [
            {"index": 1, "text": "가나다정", "recognition_score": 0.98, "polygon": _poly(10, 10)},
            {"index": 2, "text": "1정", "recognition_score": 0.99, "polygon": _poly(120, 10)},
            {"index": 3, "text": "잡음", "recognition_score": 0.72, "polygon": _poly(400, 400)},
        ]
        nodes = align_observation_nodes(gt, observed)
        self.assertEqual(nodes[0]["semantic_role"], "product")
        self.assertEqual(nodes[1]["semantic_role"], "dose")
        self.assertEqual(nodes[2]["semantic_role"], "other")
        self.assertEqual(nodes[2]["target_region_ids"], [])
        relations = build_relation_labels(nodes)
        self.assertEqual(relations, [{"product_node_id": "region-0001", "field_node_id": "region-0002", "label": "same_medication"}])

    def test_merge_across_different_semantics_is_ambiguous_and_masked(self) -> None:
        gt = [_gt("p", "product", "m1", 10, 10, 70), _gt("d", "dose", "m1", 82, 10, 50)]
        observed = [{"index": 1, "text": "가나다정1정", "recognition_score": 0.91, "polygon": _poly(8, 8, 130, 28)}]
        nodes = align_observation_nodes(gt, observed)
        self.assertEqual(nodes[0]["label_status"], "ambiguous")
        self.assertIsNone(nodes[0]["semantic_role"])
        self.assertIsNone(nodes[0]["association_group"])
        self.assertEqual(set(nodes[0]["target_region_ids"]), {"p", "d"})
        self.assertEqual(build_relation_labels(nodes), [])

    def test_split_boxes_from_same_truth_region_keep_same_label(self) -> None:
        gt = [_gt("p", "product", "m1", 10, 10, 100)]
        observed = [
            {"index": 1, "text": "가나다", "recognition_score": 0.95, "polygon": _poly(10, 10, 48)},
            {"index": 2, "text": "정", "recognition_score": 0.93, "polygon": _poly(60, 10, 50)},
        ]
        nodes = align_observation_nodes(gt, observed)
        self.assertTrue(all(node["label_status"] == "labeled" for node in nodes))
        self.assertTrue(all(node["semantic_role"] == "product" for node in nodes))
        self.assertTrue(all(node["association_group"] == "m1" for node in nodes))

    def test_instruction_edges_are_supervised_with_hard_negatives(self) -> None:
        nodes = [
            {"node_id": "p1", "label_status": "labeled", "semantic_role": "product", "association_group": "m1"},
            {"node_id": "p2", "label_status": "labeled", "semantic_role": "product", "association_group": "m2"},
            {"node_id": "i1", "label_status": "labeled", "semantic_role": "instruction", "association_group": "m1"},
        ]
        self.assertEqual(
            build_relation_labels(nodes),
            [
                {"product_node_id": "p1", "field_node_id": "i1", "label": "same_medication"},
                {"product_node_id": "p2", "field_node_id": "i1", "label": "different_medication"},
            ],
        )

    def test_schedule_edges_are_supervised_with_hard_negatives(self) -> None:
        nodes = [
            {"node_id": "p1", "label_status": "labeled", "semantic_role": "product", "association_group": "m1"},
            {"node_id": "p2", "label_status": "labeled", "semantic_role": "product", "association_group": "m2"},
            {"node_id": "s1", "label_status": "labeled", "semantic_role": "schedule", "association_group": "m1"},
        ]
        self.assertEqual(
            build_relation_labels(nodes),
            [
                {"product_node_id": "p1", "field_node_id": "s1", "label": "same_medication"},
                {"product_node_id": "p2", "field_node_id": "s1", "label": "different_medication"},
            ],
        )

    def test_input_order_only_changes_node_ids_when_runtime_indices_change(self) -> None:
        gt = [_gt("p", "product", "m1", 10, 10), _gt("d", "dose", "m1", 120, 10)]
        observed = [
            {"index": 7, "text": "1정", "recognition_score": 0.99, "polygon": _poly(120, 10)},
            {"index": 3, "text": "가나다정", "recognition_score": 0.98, "polygon": _poly(10, 10)},
        ]
        forward = align_observation_nodes(gt, observed)
        reverse = align_observation_nodes(gt, list(reversed(observed)))
        self.assertEqual(forward, reverse)


if __name__ == "__main__":
    unittest.main()
