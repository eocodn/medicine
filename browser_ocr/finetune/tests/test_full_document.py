from __future__ import annotations

import unittest

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.full_document import (
    build_document_regions,
    parse_recognition_rows,
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


if __name__ == "__main__":
    unittest.main()
