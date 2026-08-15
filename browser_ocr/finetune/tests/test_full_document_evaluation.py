from __future__ import annotations

import unittest

from browser_ocr.finetune.full_document_evaluation import evaluate_sample


def _poly(x: float, y: float, width: float = 80.0, height: float = 24.0) -> list[list[float]]:
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def _region(region_id: str, text: str, role: str, group: str, x: float, y: float) -> dict:
    return {
        "region_id": region_id,
        "text": text,
        "polygon": _poly(x, y),
        "natural_text_polygon": _poly(x, y),
        "critical": role in {"product", "dose", "frequency", "duration"},
        "association_group": group,
        "semantic_role": role,
    }


class FullDocumentEvaluationTest(unittest.TestCase):
    def sample(self) -> dict:
        return {
            "id": "doc",
            "layout_family": "fixture",
            "capture_profile": "flat_scan",
            "regions": [
                _region("a-product", "동일정", "product", "a", 10, 10),
                _region("a-dose", "1정", "dose", "a", 120, 10),
                _region("a-frequency", "2회", "frequency", "a", 220, 10),
                _region("a-duration", "5일", "duration", "a", 320, 10),
                _region("b-product", "동일정", "product", "b", 10, 60),
                _region("b-dose", "1정", "dose", "b", 120, 60),
                _region("b-frequency", "2회", "frequency", "b", 220, 60),
                _region("b-duration", "5일", "duration", "b", 320, 60),
            ],
        }

    def result(self) -> dict:
        regions = []
        for index, gt in enumerate(self.sample()["regions"], start=1):
            regions.append(
                {
                    "index": index,
                    "polygon": gt["polygon"],
                    "text": gt["text"],
                    "recognition_score": 0.99,
                }
            )
        return {
            "regions": regions,
            "medications": [
                {
                    "row_id": "region-0001",
                    "product_query": "동일정",
                    "draft": {"dose_amount": 1, "dose_unit": "tablet", "frequency_per_day": 2, "prescription_days": 5},
                    "uncertainty_codes": [],
                    "evidence": {
                        "product_query": ["region-0001"],
                        "dose_amount": ["region-0002"],
                        "dose_unit": ["region-0002"],
                        "frequency_per_day": ["region-0003"],
                        "prescription_days": ["region-0004"],
                    },
                },
                {
                    "row_id": "region-0005",
                    "product_query": "동일정",
                    "draft": {"dose_amount": 1, "dose_unit": "tablet", "frequency_per_day": 2, "prescription_days": 5},
                    "uncertainty_codes": [],
                    "evidence": {
                        "product_query": ["region-0005"],
                        "dose_amount": ["region-0006"],
                        "dose_unit": ["region-0006"],
                        "frequency_per_day": ["region-0007"],
                        "prescription_days": ["region-0008"],
                    },
                },
            ],
        }

    def test_exact_rows_are_bound_to_their_source_geometry(self) -> None:
        evaluation = evaluate_sample(self.sample(), self.result())
        self.assertEqual(evaluation["expected_rows"], 2)
        self.assertEqual(evaluation["matched_rows"], 2)
        self.assertEqual(evaluation["critical_field_exact"], 8)
        self.assertEqual(evaluation["critical_field_total"], 8)
        self.assertEqual(evaluation["cross_medication_associations"], 0)
        self.assertEqual(evaluation["unproven_associations"], 0)
        self.assertTrue(evaluation["safety_pass"])

    def test_wrong_exact_value_is_a_safety_failure_even_with_correct_association(self) -> None:
        result = self.result()
        result["medications"][0]["draft"]["dose_amount"] = 5
        evaluation = evaluate_sample(self.sample(), result)
        self.assertEqual(evaluation["false_exact_fields"], 1)
        self.assertFalse(evaluation["safety_pass"])

    def test_equal_values_from_another_medication_are_still_cross_association(self) -> None:
        result = self.result()
        result["medications"][0]["evidence"]["frequency_per_day"] = ["region-0007"]
        evaluation = evaluate_sample(self.sample(), result)
        self.assertEqual(evaluation["critical_field_exact"], 8)
        self.assertEqual(evaluation["cross_medication_associations"], 1)
        self.assertFalse(evaluation["safety_pass"])


if __name__ == "__main__":
    unittest.main()
