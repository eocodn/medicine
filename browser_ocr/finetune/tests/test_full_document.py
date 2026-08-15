from __future__ import annotations

import unittest

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.full_document import (
    build_document_regions,
    parse_document_regions,
    parse_recognition_rows,
    recognition_quality,
    regions_to_ocr_boxes,
    sort_text_predictions,
)


class FullDocumentPipelineCoreTest(unittest.TestCase):
    def test_predictions_are_sorted_in_reading_order(self) -> None:
        predictions = [
            {"polygon": [[120, 80], [180, 80], [180, 100], [120, 100]], "score": 0.9},
            {"polygon": [[20, 20], [80, 20], [80, 40], [20, 40]], "score": 0.8},
            {"polygon": [[100, 20], [160, 20], [160, 40], [100, 40]], "score": 0.7},
        ]
        ordered = sort_text_predictions(predictions)
        self.assertEqual([item["polygon"][0][0] for item in ordered], [20, 100, 120])

    def test_recognition_rows_require_exact_crop_coverage(self) -> None:
        expected = ["/run/crops/0001.png", "/run/crops/0002.png"]
        parsed = parse_recognition_rows(
            "/run/crops/0001.png\t타이레놀정\t0.99\n"
            "/run/crops/0002.png\t1일 3회\t0.97\n",
            expected,
        )
        self.assertEqual(parsed[expected[0]], {"text": "타이레놀정", "score": 0.99})
        self.assertEqual(parsed[expected[1]], {"text": "1일 3회", "score": 0.97})

        with self.assertRaisesRegex(DatasetError, "missing recognition result"):
            parse_recognition_rows("/run/crops/0001.png\t타이레놀정\t0.99\n", expected)

        with self.assertRaisesRegex(DatasetError, "duplicate recognition result"):
            parse_recognition_rows(
                "/run/crops/0001.png\ta\t0.9\n/run/crops/0001.png\tb\t0.8\n/run/crops/0002.png\tc\t0.7\n",
                expected,
            )

    def test_document_regions_preserve_detection_to_recognition_alignment(self) -> None:
        predictions = [
            {"polygon": [[10, 10], [90, 10], [90, 30], [10, 30]], "score": 0.91},
            {"polygon": [[10, 50], [90, 50], [90, 70], [10, 70]], "score": 0.83},
        ]
        crops = ["/run/crops/0001.png", "/run/crops/0002.png"]
        recognized = {
            crops[0]: {"text": "아모잘탄정", "score": 0.98},
            crops[1]: {"text": "1정", "score": 0.96},
        }
        regions = build_document_regions(predictions, crops, recognized)
        self.assertEqual([item["text"] for item in regions], ["아모잘탄정", "1정"])
        self.assertEqual(regions[0]["detection_score"], 0.91)
        self.assertEqual(regions[1]["recognition_score"], 0.96)
        self.assertEqual(regions[0]["crop"], "0001.png")

    def test_regions_are_adapted_to_parser_boxes_with_stable_evidence_ids(self) -> None:
        regions = [
            {
                "index": 1,
                "polygon": [[10, 10], [90, 10], [90, 30], [10, 30]],
                "text": "약명",
                "recognition_score": 0.99,
            },
            {
                "index": 2,
                "polygon": [[110, 10], [230, 10], [230, 30], [110, 30]],
                "text": "가나다정",
                "recognition_score": 0.98,
            },
        ]
        boxes = regions_to_ocr_boxes(regions)
        self.assertEqual([box.box_id for box in boxes], ["region-0001", "region-0002"])
        self.assertEqual(boxes[1].text, "가나다정")
        self.assertEqual(boxes[1].confidence, 0.98)

    def test_document_quality_gate_abstains_when_low_confidence_regions_are_common(self) -> None:
        def region(index: int, score: float, text: str = "가나다정") -> dict:
            return {
                "index": index,
                "polygon": [[10, 10], [90, 10], [90, 30], [10, 30]],
                "text": text,
                "recognition_score": score,
            }

        good = recognition_quality([region(1, 0.99), region(2, 0.95), region(3, 0.79), region(4, 0.99), region(5, 0.99)])
        self.assertTrue(good["safe_for_structured_parsing"])
        self.assertEqual(good["low_confidence_regions"], 1)

        degraded = recognition_quality([region(1, 0.99), region(2, 0.75), region(3, 0.70), region(4, 0.99), region(5, 0.99)])
        self.assertFalse(degraded["safe_for_structured_parsing"])
        self.assertEqual(degraded["low_confidence_regions"], 2)

    def test_empty_recognition_region_is_preserved_upstream_but_omitted_from_parser_input(self) -> None:
        regions = [
            {
                "index": 1,
                "polygon": [[10, 10], [90, 10], [90, 30], [10, 30]],
                "text": "",
                "recognition_score": 0.0,
            },
            {
                "index": 2,
                "polygon": [[10, 50], [90, 50], [90, 70], [10, 70]],
                "text": "가나다정",
                "recognition_score": 0.99,
            },
        ]
        boxes = regions_to_ocr_boxes(regions)
        self.assertEqual([box.box_id for box in boxes], ["region-0002"])

    def test_full_document_regions_parse_to_evidence_backed_medication_rows(self) -> None:
        def region(index: int, text: str, x: float, y: float, width: float = 90.0) -> dict:
            return {
                "index": index,
                "polygon": [[x, y], [x + width, y], [x + width, y + 24], [x, y + 24]],
                "text": text,
                "recognition_score": 0.99,
            }

        rows = parse_document_regions(
            [
                region(1, "약명", 20, 20, 70),
                region(2, "가나다정", 120, 20, 130),
                region(3, "1정", 120, 70, 60),
                region(4, "2회", 220, 70, 60),
                region(5, "5일", 320, 70, 60),
                region(6, "약명", 20, 160, 70),
                region(7, "라마바정", 120, 160, 130),
                region(8, "2정", 120, 210, 60),
                region(9, "3회", 220, 210, 60),
                region(10, "7일", 320, 210, 60),
            ]
        )
        self.assertEqual([row["product_query"] for row in rows], ["가나다정", "라마바정"])
        self.assertEqual(rows[0]["row_id"], "region-0001")
        self.assertEqual(rows[0]["evidence"]["dose_amount"], ["region-0003"])
        self.assertEqual(rows[1]["evidence"]["prescription_days"], ["region-0010"])


if __name__ == "__main__":
    unittest.main()
