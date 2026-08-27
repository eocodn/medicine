from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_dataset import load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_development_views import (
    DEVELOPMENT_VIEWS,
    build_parser_v5_development_views,
)


class ParserV5DevelopmentViewsTest(unittest.TestCase):
    def test_default_matrix_covers_independent_generalization_axes(self) -> None:
        names = {view.name for view in DEVELOPMENT_VIEWS}
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

    def test_view_builder_creates_disjoint_hash_bound_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            manifests = build_parser_v5_development_views(raw, documents_per_view=2, seed=624)
            datasets = {name: load_parser_v5_dataset(path) for name, path in manifests.items()}
            self.assertEqual(set(datasets), {view.name for view in DEVELOPMENT_VIEWS})
            self.assertEqual(len({dataset.samples_sha256 for dataset in datasets.values()}), len(datasets))
            zero = datasets["zero_medication"]
            self.assertTrue(all(not sample["truth"]["medications"] for sample in zero.samples))
            many = datasets["many_medication"]
            self.assertTrue(all(len(sample["truth"]["medications"]) >= 5 for sample in many.samples))


if __name__ == "__main__":
    unittest.main()