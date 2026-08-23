from __future__ import annotations

import unittest
from unittest.mock import patch

from browser_ocr.finetune.orientation_runtime import resolve_page_orientation


class _Image:
    def __init__(self, height: int, width: int) -> None:
        self.shape = (height, width, 3)


class OrientationRuntimeTest(unittest.TestCase):
    def test_vertical_detector_boxes_use_bounded_probe_and_canonicalize_geometry(self) -> None:
        image = _Image(100, 200)
        predictions = [{
            "polygon": [[10, 10], [30, 10], [30, 90], [10, 90]],
            "score": 0.95,
        }]
        crop = object()
        with patch(
            "browser_ocr.finetune.orientation_runtime.refine_prediction_crops",
            side_effect=lambda _image, items: [(dict(items[0]), crop)],
        ), patch(
            "browser_ocr.finetune.orientation_runtime.rotate_image_right_angle",
            side_effect=lambda _image, rotation: _Image(200, 100) if rotation in {90, 270} else _Image(100, 200),
        ):
            canonical, transformed, decision = resolve_page_orientation(
                image,
                predictions,
                probe_scorer=lambda probes: {
                    90: 0.20 if 90 in probes else 0.0,
                    270: 0.91 if 270 in probes else 0.0,
                },
            )
        self.assertEqual(decision["candidates"], [90, 270])
        self.assertEqual(decision["applied_rotation_degrees"], 270)
        self.assertEqual(canonical.shape[:2], (200, 100))
        self.assertEqual(transformed[0]["polygon"], [[10.0, 170.0], [90.0, 170.0], [90.0, 190.0], [10.0, 190.0]])


if __name__ == "__main__":
    unittest.main()