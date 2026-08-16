from __future__ import annotations

import unittest

from browser_ocr.corpus.materialize_helpers import _ordered_crop_batches


class RecognitionCropBatchingTest(unittest.TestCase):
    def test_groups_contiguous_source_images_into_bounded_ordered_batches(self) -> None:
        jobs = [
            {"image": "a.jpg", "output": "a-1.png"},
            {"image": "a.jpg", "output": "a-2.png"},
            {"image": "b.jpg", "output": "b-1.png"},
            {"image": "c.jpg", "output": "c-1.png"},
            {"image": "c.jpg", "output": "c-2.png"},
            {"image": "d.jpg", "output": "d-1.png"},
        ]

        batches = _ordered_crop_batches(jobs, start_index=1, worker_count=2)

        self.assertEqual(
            [
                [[1, 2], [3]],
                [[4, 5], [6]],
            ],
            [[[ordinal for ordinal, _job in group] for group in batch] for batch in batches],
        )
        self.assertEqual(
            [["a.jpg", "b.jpg"], ["c.jpg", "d.jpg"]],
            [[group[0][1]["image"] for group in batch] for batch in batches],
        )

    def test_resume_can_start_inside_a_source_image_group(self) -> None:
        jobs = [
            {"image": "a.jpg", "output": "a-1.png"},
            {"image": "a.jpg", "output": "a-2.png"},
            {"image": "a.jpg", "output": "a-3.png"},
            {"image": "b.jpg", "output": "b-1.png"},
        ]

        batches = _ordered_crop_batches(jobs[1:], start_index=2, worker_count=4)

        self.assertEqual(
            [[[2, 3], [4]]],
            [[[ordinal for ordinal, _job in group] for group in batch] for batch in batches],
        )


if __name__ == "__main__":
    unittest.main()