import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from browser_ocr.detection.detector_benchmark import (
    db_postprocess,
    resize_dimensions,
    validate_official_config,
)


class DetectorBenchmarkCoreTest(unittest.TestCase):
    def test_resize_dimensions_preserve_aspect_and_multiple_of_32(self):
        self.assertEqual(resize_dimensions(1280, 1600, 640), (512, 640))
        self.assertEqual(resize_dimensions(1280, 1600, 960), (768, 960))
        self.assertEqual(resize_dimensions(1280, 1600, 1280), (1024, 1280))
        width, height = resize_dimensions(641, 120, 640)
        self.assertEqual(width % 32, 0)
        self.assertEqual(height % 32, 0)
        self.assertLessEqual(max(width, height), 640 + 16)

    def test_db_postprocess_extracts_one_box_from_probability_map(self):
        probability = np.zeros((64, 64), dtype=np.float32)
        probability[20:44, 14:50] = 0.95
        boxes = db_postprocess(
            probability,
            source_width=640,
            source_height=640,
            threshold=0.3,
            box_threshold=0.6,
            max_candidates=1000,
            unclip_ratio=1.5,
        )
        self.assertEqual(len(boxes), 1)
        self.assertGreater(boxes[0]["score"], 0.9)
        self.assertEqual(len(boxes[0]["polygon"]), 4)
        for x, y in boxes[0]["polygon"]:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x, 640)
            self.assertLessEqual(y, 640)

    def test_official_inference_yaml_must_match_pinned_db_settings(self):
        pinned = {
            "preprocess": {
                "color_mode": "BGR",
                "mean": [0.485, 0.456, 0.406],
                "std": [0.229, 0.224, 0.225],
            },
            "postprocess": {
                "threshold": 0.2,
                "box_threshold": 0.45,
                "max_candidates": 3000,
                "unclip_ratio": 1.4,
            },
        }
        config = """Global:\n  model_name: PP-OCRv6_small_det\nPreProcess:\n  transform_ops:\n  - DecodeImage:\n      channel_first: false\n      img_mode: BGR\n  - NormalizeImage:\n      mean: [0.485, 0.456, 0.406]\n      order: hwc\n      scale: 1./255.\n      std: [0.229, 0.224, 0.225]\nPostProcess:\n  name: DBPostProcess\n  thresh: 0.2\n  box_thresh: 0.45\n  max_candidates: 3000\n  unclip_ratio: 1.4\n"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference.yml"
            path.write_text(config, encoding="utf-8")
            validate_official_config(path, "PP-OCRv6_small_det", pinned)
            broken = json.loads(json.dumps(pinned))
            broken["postprocess"]["box_threshold"] = 0.6
            with self.assertRaisesRegex(ValueError, "box_threshold"):
                validate_official_config(path, "PP-OCRv6_small_det", broken)


if __name__ == "__main__":
    unittest.main()