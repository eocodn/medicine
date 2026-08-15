from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.learned_experiment import (
    aggregate_samples,
    document_from_detection_sample,
    document_from_sample_result,
    evaluate_learned_result,
    load_semantic_examples,
)
from browser_ocr.document_parsing.learned_layout import train_model


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


class LearnedExperimentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = {
            "id": "sample-1",
            "width": 640,
            "height": 200,
            "layout_family": "table",
            "capture_profile": "clean",
            "regions": [
                {"region_id": "p1", "text": "가나다정", "polygon": _poly(10, 50), "natural_text_polygon": _poly(10, 50), "critical": True, "semantic_role": "product", "association_group": "a"},
                {"region_id": "d1", "text": "1정", "polygon": _poly(220, 50), "natural_text_polygon": _poly(220, 50), "critical": True, "semantic_role": "dose", "association_group": "a"},
                {"region_id": "f1", "text": "2회", "polygon": _poly(360, 50), "natural_text_polygon": _poly(360, 50), "critical": True, "semantic_role": "frequency", "association_group": "a"},
                {"region_id": "t1", "text": "5일", "polygon": _poly(500, 50), "natural_text_polygon": _poly(500, 50), "critical": True, "semantic_role": "duration", "association_group": "a"},
                {"region_id": "title", "text": "처방전", "polygon": _poly(10, 5), "natural_text_polygon": _poly(10, 5), "critical": False, "semantic_role": "document_title", "association_group": "document"},
            ],
        }
        self.result = {
            "status": "ok",
            "image": {"width": 640, "height": 200},
            "regions": [
                {"index": 1, "text": "가나다정", "recognition_score": 0.99, "polygon": _poly(10, 50)},
                {"index": 2, "text": "1정", "recognition_score": 0.99, "polygon": _poly(220, 50)},
                {"index": 3, "text": "2회", "recognition_score": 0.99, "polygon": _poly(360, 50)},
                {"index": 4, "text": "5일", "recognition_score": 0.99, "polygon": _poly(500, 50)},
                {"index": 5, "text": "처방전", "recognition_score": 0.99, "polygon": _poly(10, 5)},
                {"index": 6, "text": "잡음", "recognition_score": 0.60, "polygon": _poly(560, 160)},
            ],
            "medications": [],
        }

    def test_detection_gt_document_preserves_full_layout_context(self) -> None:
        document = document_from_detection_sample(self.sample)
        self.assertEqual(document.sample_id, "sample-1")
        self.assertEqual(len(document.nodes), 5)
        by_text = {node.text: node for node in document.nodes}
        self.assertEqual(by_text["가나다정"].role, "product")
        self.assertEqual(by_text["1정"].role, "dose")
        self.assertEqual(by_text["처방전"].role, "other")
        self.assertEqual(by_text["처방전"].group, None)
        self.assertEqual(by_text["가나다정"].confidence, 1.0)

    def test_document_maps_ocr_regions_to_gt_roles_and_marks_unmatched_as_other(self) -> None:
        document = document_from_sample_result(self.sample, self.result)
        labels = {node.box_id: (node.role, node.group) for node in document.nodes}
        self.assertEqual(labels["region-0001"], ("product", "a"))
        self.assertEqual(labels["region-0002"], ("dose", "a"))
        self.assertEqual(labels["region-0005"], ("other", None))
        self.assertEqual(labels["region-0006"], ("other", None))

    def test_learned_result_uses_same_source_region_evidence_contract(self) -> None:
        documents = [document_from_sample_result(self.sample, self.result) for _ in range(18)]
        model = train_model(documents, epochs=70, seed=4)
        evaluated = evaluate_learned_result(self.sample, self.result, model)
        self.assertEqual(evaluated["matched_rows"], 1)
        self.assertEqual(evaluated["critical_field_exact"], 4)
        self.assertEqual(evaluated["cross_medication_associations"], 0)
        self.assertTrue(evaluated["safety_pass"])

    def test_aggregate_samples_reports_safety_and_quality_separately(self) -> None:
        summary = aggregate_samples(
            [
                {"expected_rows": 2, "predicted_rows": 2, "matched_rows": 2, "missing_rows": 0, "unexpected_rows": 0, "critical_field_exact": 8, "critical_field_total": 8, "false_exact_fields": 0, "unresolved_fields": 0, "critical_detection_matched": 8, "critical_detection_total": 8, "cross_medication_associations": 0, "unproven_associations": 0, "safety_pass": True, "quality_pass": True, "layout_family": "a", "capture_profile": "clean"},
                {"expected_rows": 2, "predicted_rows": 0, "matched_rows": 0, "missing_rows": 2, "unexpected_rows": 0, "critical_field_exact": 0, "critical_field_total": 8, "false_exact_fields": 0, "unresolved_fields": 0, "critical_detection_matched": 8, "critical_detection_total": 8, "cross_medication_associations": 0, "unproven_associations": 0, "safety_pass": True, "quality_pass": False, "layout_family": "a", "capture_profile": "motion"},
            ]
        )
        self.assertEqual(summary["safety_pass_samples"], 2)
        self.assertEqual(summary["quality_pass_samples"], 1)
        self.assertEqual(summary["metrics"]["row_recall"], 0.5)

    def test_semantic_example_loader_balances_roles_deterministically(self) -> None:
        rows = []
        for index in range(20):
            rows.extend(
                [
                    {"id": f"p-{index}", "text": f"가나다정{index}", "semantic_tags": ["product"]},
                    {"id": f"d-{index}", "text": "1정", "semantic_tags": ["dose"]},
                    {"id": f"f-{index}", "text": "1일 3회", "semantic_tags": ["frequency"]},
                    {"id": f"t-{index}", "text": "5일간", "semantic_tags": ["duration"]},
                    {"id": f"o-{index}", "text": "주의사항", "semantic_tags": ["instruction"]},
                ]
            )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "samples.jsonl"
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            first, first_meta = load_semantic_examples(path, per_role=7, seed=91)
            second, second_meta = load_semantic_examples(path, per_role=7, seed=91)
        self.assertEqual(first, second)
        self.assertEqual(first_meta, second_meta)
        self.assertEqual(len(first), 35)
        self.assertEqual(
            first_meta["role_counts"],
            {"product": 7, "dose": 7, "frequency": 7, "duration": 7, "other": 7},
        )


if __name__ == "__main__":
    unittest.main()