from __future__ import annotations

import unittest

from browser_ocr.document_parsing.evaluation import evaluate_parser_document
from browser_ocr.document_parsing.graph_decode import DecodeConfig, decode_graph_scores


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _node(node_id: str, text: str, role: str, group: str | None, x: float, y: float) -> dict[str, object]:
    return {
        "node_id": node_id,
        "text": text,
        "confidence": 0.98,
        "polygon": _poly(x, y),
        "target_region_ids": [f"gt-{node_id}"],
        "label_status": "labeled",
        "semantic_role": role,
        "association_group": group,
    }


def _document() -> dict[str, object]:
    return {
        "document_id": "decode-fixture",
        "split": "val",
        "source_kind": "synthetic",
        "image_sha256": "a" * 64,
        "width": 1000,
        "height": 1400,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription"],
        "risk_tags": ["row_association"],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "provenance": None,
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": "b" * 64},
        "observation": {
            "kind": "runtime_ocr",
            "profile": {},
            "nodes": [
                _node("p1", "약품명: 가나다정", "product", "m1", 80, 120),
                _node("d1", "1정", "dose", "m1", 360, 120),
                _node("f1", "1일 3회", "frequency", "m1", 500, 120),
                _node("t1", "5일분", "duration", "m1", 650, 120),
                _node("i1", "식후 경구 복용", "instruction", "m1", 360, 170),
                _node("p2", "라마바정", "product", "m2", 80, 320),
                _node("d2", "2정", "dose", "m2", 360, 320),
                _node("f2", "1일 2회", "frequency", "m2", 500, 320),
                _node("t2", "7일", "duration", "m2", 650, 320),
                _node("o1", "17,100", "other", None, 720, 900),
            ],
        },
        "relations": [],
        "gold_rows": [
            {
                "gold_row_id": "m1",
                "product_query": "가나다정",
                "draft": {
                    "dose_amount": 1,
                    "dose_unit": "tablet",
                    "frequency_per_day": 3,
                    "prescription_days": 5,
                    "meal_relation": "after_meal",
                    "administration_route": "oral",
                },
            },
            {
                "gold_row_id": "m2",
                "product_query": "라마바정",
                "draft": {
                    "dose_amount": 2,
                    "dose_unit": "tablet",
                    "frequency_per_day": 2,
                    "prescription_days": 7,
                },
            },
        ],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


def _role_scores() -> dict[str, dict[str, float]]:
    roles = ("product", "product_label", "dose", "frequency", "duration", "instruction", "schedule", "header", "other")
    actual = {
        "p1": "product", "d1": "dose", "f1": "frequency", "t1": "duration", "i1": "instruction",
        "p2": "product", "d2": "dose", "f2": "frequency", "t2": "duration", "o1": "other",
    }
    result: dict[str, dict[str, float]] = {}
    for node_id, role in actual.items():
        values = {candidate: 0.005 for candidate in roles}
        values[role] = 0.96
        result[node_id] = values
    return result


def _association_scores() -> dict[tuple[str, str], float]:
    fields = ["d1", "f1", "t1", "i1", "d2", "f2", "t2"]
    scores: dict[tuple[str, str], float] = {}
    for product in ["p1", "p2"]:
        for field in fields:
            same = field.endswith("1") and product == "p1" or field.endswith("2") and product == "p2"
            scores[(product, field)] = 0.97 if same else 0.03
    return scores


class GraphDecodeTest(unittest.TestCase):
    def test_confident_learned_roles_and_associations_decode_typed_rows_with_evidence(self) -> None:
        rows = decode_graph_scores(_document(), _role_scores(), _association_scores())
        self.assertEqual([row["row_id"] for row in rows], ["p1", "p2"])
        first = rows[0]
        self.assertEqual(first["product_query"], "가나다정")
        self.assertEqual(first["draft"]["dose_amount"], 1)
        self.assertEqual(first["draft"]["dose_unit"], "tablet")
        self.assertEqual(first["draft"]["frequency_per_day"], 3)
        self.assertEqual(first["draft"]["prescription_days"], 5)
        self.assertEqual(first["draft"]["meal_relation"], "after_meal")
        self.assertEqual(first["draft"]["administration_route"], "oral")
        self.assertEqual(first["evidence"]["frequency_per_day"], ["f1"])
        self.assertEqual(first["uncertainty_codes"], [])

        evaluation = evaluate_parser_document(_document(), rows)
        self.assertEqual(evaluation["false_exact_fields"], 0)
        self.assertEqual(evaluation["cross_medication_associations"], 0)
        self.assertTrue(evaluation["safety_pass"])

    def test_relation_margin_failure_leaves_field_unresolved_instead_of_borrowing_other_row(self) -> None:
        scores = _association_scores()
        scores[("p1", "f1")] = 0.82
        scores[("p2", "f1")] = 0.78
        rows = decode_graph_scores(
            _document(),
            _role_scores(),
            scores,
            config=DecodeConfig(relation_threshold=0.7, relation_margin=0.1),
        )
        first = next(row for row in rows if row["row_id"] == "p1")
        self.assertNotIn("frequency_per_day", first["draft"])
        self.assertIn("AMBIGUOUS_ASSOCIATION", first["uncertainty_codes"])
        evaluation = evaluate_parser_document(_document(), rows)
        self.assertEqual(evaluation["unresolved_fields"], 1)
        self.assertEqual(evaluation["cross_medication_associations"], 0)
        self.assertEqual(evaluation["false_exact_fields"], 0)

    def test_wrong_high_confidence_association_is_visible_as_cross_medication_safety_error(self) -> None:
        scores = _association_scores()
        scores[("p1", "f2")] = 0.02
        scores[("p2", "f2")] = 0.03
        scores[("p1", "f1")] = 0.01
        scores[("p2", "f1")] = 0.99
        rows = decode_graph_scores(_document(), _role_scores(), scores)
        second = next(row for row in rows if row["row_id"] == "p2")
        self.assertEqual(second["draft"]["frequency_per_day"], 3)
        self.assertEqual(second["evidence"]["frequency_per_day"], ["f1"])
        evaluation = evaluate_parser_document(_document(), rows)
        self.assertGreaterEqual(evaluation["cross_medication_associations"], 1)
        self.assertFalse(evaluation["safety_pass"])

    def test_low_confidence_product_is_not_emitted_as_a_row(self) -> None:
        role_scores = _role_scores()
        role_scores["p2"]["product"] = 0.55
        role_scores["p2"]["other"] = 0.4
        rows = decode_graph_scores(_document(), role_scores, _association_scores())
        self.assertEqual([row["row_id"] for row in rows], ["p1"])


if __name__ == "__main__":
    unittest.main()