from __future__ import annotations

import unittest
from pathlib import Path

from browser_ocr.document_parsing.baseline import parse_boxes, run_baseline
from browser_ocr.document_parsing.contract import OcrBox, load_corpus


CORPUS = Path("browser_ocr/document_parsing/corpus/manifest.json")


def _box(box_id: str, text: str, y: float) -> OcrBox:
    return OcrBox(
        box_id=box_id,
        text=text,
        confidence=1.0,
        polygon=((20.0, y), (420.0, y), (420.0, y + 24.0), (20.0, y + 24.0)),
    )


def _xybox(box_id: str, text: str, x: float, y: float, width: float = 100.0) -> OcrBox:
    return OcrBox(
        box_id=box_id,
        text=text,
        confidence=1.0,
        polygon=((x, y), (x + width, y), (x + width, y + 24.0), (x, y + 24.0)),
    )


class GeometryRuleBaselineTest(unittest.TestCase):
    def test_seed_corpus_is_parsed_without_false_exact_associations(self) -> None:
        result = run_baseline(load_corpus(CORPUS))

        self.assertTrue(result["evaluation"]["safety_pass"])
        self.assertEqual(result["evaluation"]["cross_medication_associations"], 0)
        self.assertEqual(result["evaluation"]["unexpected_rows"], 0)
        self.assertEqual(result["evaluation"]["row_recall"], 1.0)
        self.assertEqual(result["evaluation"]["field_exact_accuracy"], 1.0)

    def test_unlabeled_regimen_after_multiple_products_is_left_unresolved(self) -> None:
        rows = parse_boxes(
            (
                _box("a", "약명: 가나다정", 20),
                _box("b", "약명: 라마바정", 52),
                _box("r", "1회 1정 1일 3회 5일", 84),
            )
        )

        self.assertEqual([row["product_query"] for row in rows], ["가나다정", "라마바정"])
        self.assertEqual(rows[0]["draft"], {})
        self.assertEqual(rows[1]["draft"], {})
        self.assertIn("UNRESOLVED_REGIMEN_ASSOCIATION", rows[0]["uncertainty_codes"])
        self.assertIn("UNRESOLVED_REGIMEN_ASSOCIATION", rows[1]["uncertainty_codes"])

    def test_table_structure_survives_non_product_header_ocr_errors(self) -> None:
        rows = parse_boxes(
            (
                _xybox("h1", "약품명", 20, 20, 120),
                _xybox("h2", "1회 투약량", 220, 20, 130),
                _xybox("h3", "1일 핏수", 420, 20, 130),
                _xybox("h4", "종 일수", 620, 20, 120),
                _xybox("p1", "가나다정", 20, 70, 130),
                _xybox("d1", "1정", 240, 70, 70),
                _xybox("f1", "3회", 440, 70, 70),
                _xybox("t1", "5일", 640, 70, 70),
                _xybox("p2", "라마바정", 20, 120, 130),
                _xybox("d2", "2정", 240, 120, 70),
                _xybox("f2", "2회", 440, 120, 70),
                _xybox("t2", "7일", 640, 120, 70),
            )
        )

        self.assertEqual([row["product_query"] for row in rows], ["가나다정", "라마바정"])
        self.assertEqual(
            rows[0]["draft"],
            {"dose_amount": 1, "dose_unit": "tablet", "frequency_per_day": 3, "prescription_days": 5},
        )
        self.assertEqual(
            rows[1]["draft"],
            {"dose_amount": 2, "dose_unit": "tablet", "frequency_per_day": 2, "prescription_days": 7},
        )

    def test_repeated_bag_blocks_use_geometry_when_label_and_product_are_separate_boxes(self) -> None:
        rows = parse_boxes(
            (
                _xybox("l1", "약명", 20, 20, 70),
                _xybox("p1", "다라정", 120, 20, 130),
                _xybox("d1", "1정", 120, 70, 60),
                _xybox("f1", "2회", 220, 70, 60),
                _xybox("t1", "5일", 320, 70, 60),
                _xybox("i1", "매 식후 30분", 430, 70, 180),
                _xybox("l2", "약명", 20, 160, 70),
                _xybox("p2", "마바정", 120, 160, 130),
                _xybox("d2", "2정", 120, 210, 60),
                _xybox("f2", "3회", 220, 210, 60),
                _xybox("t2", "7일", 320, 210, 60),
                _xybox("i2", "아침 저녁 식후", 430, 210, 180),
            )
        )

        self.assertEqual([row["product_query"] for row in rows], ["다라정", "마바정"])
        self.assertEqual(rows[0]["draft"]["dose_amount"], 1)
        self.assertEqual(rows[0]["draft"]["frequency_per_day"], 2)
        self.assertEqual(rows[0]["draft"]["prescription_days"], 5)
        self.assertEqual(rows[1]["draft"]["dose_amount"], 2)
        self.assertEqual(rows[1]["draft"]["frequency_per_day"], 3)
        self.assertEqual(rows[1]["draft"]["prescription_days"], 7)
        self.assertEqual(rows[0]["evidence"]["product_query"], ["l1", "p1"])
        self.assertEqual(rows[1]["evidence"]["product_query"], ["l2", "p2"])


    def test_unheaded_repeated_rows_are_parsed_from_value_columns(self) -> None:
        rows = parse_boxes(
            (
                _xybox("p1", "가나다정", 20, 20, 130),
                _xybox("d1", "1정", 240, 20, 70),
                _xybox("f1", "2회", 440, 20, 70),
                _xybox("t1", "5일", 640, 20, 70),
                _xybox("p2", "라마바정", 20, 80, 130),
                _xybox("d2", "2정", 240, 80, 70),
                _xybox("f2", "3회", 440, 80, 70),
                _xybox("t2", "7일", 640, 80, 70),
            )
        )

        self.assertEqual([row["product_query"] for row in rows], ["가나다정", "라마바정"])
        self.assertEqual(rows[0]["draft"]["frequency_per_day"], 2)
        self.assertEqual(rows[1]["draft"]["prescription_days"], 7)

    def test_single_product_can_bind_one_unique_preprinted_regimen_above(self) -> None:
        rows = parse_boxes(
            (
                _xybox("daily", "1일", 20, 20, 60),
                _xybox("freq", "3회", 100, 20, 60),
                _xybox("each", "1회", 200, 20, 60),
                _xybox("dose", "1포(정)", 280, 20, 100),
                _xybox("total", "총", 420, 20, 50),
                _xybox("days", "5일분", 490, 20, 80),
                _xybox("label", "약품명", 20, 170, 80),
                _xybox("product", "메트포르민정", 120, 170, 150),
            )
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_query"], "메트포르민정")
        self.assertEqual(rows[0]["draft"]["dose_amount"], 1)
        self.assertEqual(rows[0]["draft"]["dosage_text"], "1포(정)")
        self.assertEqual(rows[0]["draft"]["frequency_per_day"], 3)
        self.assertEqual(rows[0]["draft"]["prescription_days"], 5)


if __name__ == "__main__":
    unittest.main()