from __future__ import annotations

import unittest

from browser_ocr.finetune.orientation import (
    canonicalize_predictions,
    orientation_candidates,
    select_orientation,
    transform_polygon_right_angle,
)


def _box(x: float, y: float, width: float, height: float, score: float = 0.9) -> dict[str, object]:
    return {
        "polygon": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
        "score": score,
    }


class OrientationTest(unittest.TestCase):
    def test_dominant_text_axis_reduces_orientation_to_two_candidates(self) -> None:
        horizontal = [_box(10, 10 + index * 30, 180, 20) for index in range(5)]
        vertical = [_box(10 + index * 30, 10, 20, 180) for index in range(5)]
        self.assertEqual(orientation_candidates(horizontal), (0, 180))
        self.assertEqual(orientation_candidates(vertical), (90, 270))

    def test_ambiguous_axis_keeps_all_four_candidates(self) -> None:
        predictions = [_box(10, 10, 100, 100), _box(150, 10, 90, 100)]
        self.assertEqual(orientation_candidates(predictions), (0, 90, 180, 270))

    def test_probe_selection_requires_margin_and_fails_closed_to_no_rotation(self) -> None:
        self.assertEqual(select_orientation({0: 0.82, 180: 0.35}), 0)
        self.assertEqual(select_orientation({90: 0.25, 270: 0.81}), 270)
        self.assertEqual(select_orientation({0: 0.61, 180: 0.58}), 0)

    def test_right_angle_transform_reorders_quad_and_updates_dimensions(self) -> None:
        polygon = [[10, 20], [110, 20], [110, 50], [10, 50]]
        rotated = transform_polygon_right_angle(polygon, width=200, height=100, degrees=90)
        self.assertEqual(rotated, [[50.0, 10.0], [80.0, 10.0], [80.0, 110.0], [50.0, 110.0]])
        predictions, width, height = canonicalize_predictions(
            [{"polygon": polygon, "score": 0.9}],
            width=200,
            height=100,
            degrees=90,
        )
        self.assertEqual((width, height), (100, 200))
        self.assertEqual(predictions[0]["polygon"], rotated)


if __name__ == "__main__":
    unittest.main()