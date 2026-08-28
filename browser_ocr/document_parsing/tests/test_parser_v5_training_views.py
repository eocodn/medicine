from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_dataset import load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_training_views import (
    TRAINING_VIEWS,
    build_parser_v5_training_views,
)


class ParserV5TrainingViewsTest(unittest.TestCase):
    def test_recipe_covers_stress_axes_without_unseen_vocabulary(self) -> None:
        names = {view.name for view in TRAINING_VIEWS}
        self.assertEqual(
            names,
            {
                "baseline",
                "zero_medication",
                "many_medication",
                "high_distractor",
                "counterfactual_context",
                "geometry_scramble",
                "ocr_corruption",
                "merged_regions",
            },
        )
        self.assertTrue(all(view.world_profile.product_vocabulary == "train" for view in TRAINING_VIEWS))
        self.assertTrue(all(view.world_profile.wording_vocabulary == "train" for view in TRAINING_VIEWS))
        self.assertGreater(next(view.sample_multiplier for view in TRAINING_VIEWS if view.name == "baseline"), 1)

    def test_builder_materializes_deterministic_relative_sample_counts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifests = build_parser_v5_training_views(raw, documents_per_unit=2, seed=624)
            datasets = {name: load_parser_v5_dataset(path) for name, path in manifests.items()}
            expected = {view.name: view.sample_multiplier * 2 for view in TRAINING_VIEWS}
            self.assertEqual({name: len(dataset.samples) for name, dataset in datasets.items()}, expected)
            self.assertEqual(len({dataset.samples_sha256 for dataset in datasets.values()}), len(datasets))


if __name__ == "__main__":
    unittest.main()