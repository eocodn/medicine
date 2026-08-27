from __future__ import annotations

import unittest

from browser_ocr.document_parsing.parser_v5_evaluation import evaluate_parser_v5_rows
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV5EvaluationTest(unittest.TestCase):
    def _pair(self, medications: int = 2):
        truth = generate_parser_world(
            seed=812,
            document_index=4,
            profile=ParserWorldProfile(
                medication_count=(medications, medications),
                distractor_section_count=(1, 1),
            ),
        )
        observation = simulate_observations(
            truth,
            seed=813,
            profile=ObservationProfile(
                text_corruption_rate=0,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=0,
                geometry_jitter=0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0,
            ),
        )
        return truth, observation

    def test_exact_rows_report_product_and_field_exact_without_safety_errors(self) -> None:
        truth, observation = self._pair()
        rows = []
        for medication in truth["medications"]:
            group = medication["medication_id"]
            product = next(
                node for node in observation["nodes"]
                if any(t["semantic_role"] == "product" and t["association_group"] == group for t in node["targets"])
            )
            fields = {}
            for role in ("dose", "frequency", "duration", "instruction", "schedule"):
                node = next(
                    node for node in observation["nodes"]
                    if any(t["semantic_role"] == role and t["association_group"] == group for t in node["targets"])
                )
                fields[role] = {"text": node["text"], "node_id": node["node_id"], "confidence": 0.9}
            rows.append({"product_node_id": product["node_id"], "product_query": product["text"], "fields": fields})

        metrics = evaluate_parser_v5_rows(truth, observation, rows)
        self.assertEqual(metrics["product_precision"], 1.0)
        self.assertEqual(metrics["product_recall"], 1.0)
        self.assertEqual(metrics["field_exact"], metrics["field_total"])
        self.assertEqual(metrics["field_false_exact"], 0)
        self.assertEqual(metrics["field_unresolved"], 0)
        self.assertEqual(metrics["cross_medication_associations"], 0)
        self.assertEqual(metrics["invented_values"], 0)

    def test_wrong_row_assignment_is_visible_as_cross_medication_and_false_exact(self) -> None:
        truth, observation = self._pair()
        first, second = [item["medication_id"] for item in truth["medications"]]
        product = next(
            node for node in observation["nodes"]
            if any(t["semantic_role"] == "product" and t["association_group"] == first for t in node["targets"])
        )
        wrong_dose = next(
            node for node in observation["nodes"]
            if any(t["semantic_role"] == "dose" and t["association_group"] == second for t in node["targets"])
        )
        rows = [{
            "product_node_id": product["node_id"],
            "product_query": product["text"],
            "fields": {"dose": {"text": wrong_dose["text"], "node_id": wrong_dose["node_id"], "confidence": 0.9}},
        }]
        metrics = evaluate_parser_v5_rows(truth, observation, rows)
        self.assertEqual(metrics["cross_medication_associations"], 1)
        self.assertEqual(metrics["field_false_exact"], 1)
        self.assertGreater(metrics["field_unresolved"], 0)

    def test_zero_medication_false_rows_and_invented_values_are_explicit(self) -> None:
        truth, observation = self._pair(medications=0)
        rows = [{
            "product_node_id": "invented-product",
            "product_query": "없는약",
            "fields": {"dose": {"text": "99정", "node_id": "invented-dose", "confidence": 0.9}},
        }]
        metrics = evaluate_parser_v5_rows(truth, observation, rows)
        self.assertEqual(metrics["zero_medication_false_rows"], 1)
        self.assertEqual(metrics["product_fp"], 1)
        self.assertEqual(metrics["invented_values"], 1)


if __name__ == "__main__":
    unittest.main()