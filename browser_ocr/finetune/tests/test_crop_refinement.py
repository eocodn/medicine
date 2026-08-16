from __future__ import annotations

import unittest

from browser_ocr.finetune.crop_refinement import horizontal_subpolygon, split_horizontal_ink_ranges


class CropRefinementTest(unittest.TestCase):
    def test_large_internal_blank_gap_splits_two_meaningful_text_spans(self) -> None:
        height = 60
        ink = [0] * 200
        for column in range(10, 90):
            ink[column] = 12
        for column in range(130, 185):
            ink[column] = 12

        self.assertEqual(split_horizontal_ink_ranges(ink, crop_height=height), [(0, 110), (110, 200)])

    def test_large_gap_trims_tiny_adjacent_fragment_instead_of_emitting_it(self) -> None:
        height = 60
        ink = [0] * 180
        for column in range(4, 11):
            ink[column] = 12
        for column in range(60, 160):
            ink[column] = 12

        self.assertEqual(split_horizontal_ink_ranges(ink, crop_height=height), [(35, 180)])

    def test_normal_word_spacing_does_not_split(self) -> None:
        height = 60
        ink = [0] * 200
        for column in range(10, 90):
            ink[column] = 12
        for column in range(112, 190):
            ink[column] = 12

        self.assertEqual(split_horizontal_ink_ranges(ink, crop_height=height), [(0, 200)])

    def test_horizontal_subpolygon_maps_crop_range_back_to_source_quad(self) -> None:
        polygon = [[10.0, 20.0], [210.0, 30.0], [200.0, 90.0], [0.0, 80.0]]

        self.assertEqual(
            horizontal_subpolygon(polygon, start=50, end=150, crop_width=200),
            [[60.0, 22.5], [160.0, 27.5], [150.0, 87.5], [50.0, 82.5]],
        )


if __name__ == "__main__":
    unittest.main()