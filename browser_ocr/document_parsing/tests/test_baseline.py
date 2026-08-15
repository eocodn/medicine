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


if __name__ == "__main__":
    unittest.main()