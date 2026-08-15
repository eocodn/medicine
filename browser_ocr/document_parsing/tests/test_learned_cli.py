from __future__ import annotations

import unittest

from browser_ocr.document_parsing.learned_cli import build_parser


class LearnedCliTest(unittest.TestCase):
    def test_benchmark_defaults_are_bounded_and_reproducible(self) -> None:
        args = build_parser().parse_args(
            [
                "benchmark",
                "--corpus",
                "/data/corpus.json",
                "--results-root",
                "/data/results",
                "--output-dir",
                "/data/out",
            ]
        )
        self.assertEqual(args.epochs, 60)
        self.assertEqual(args.seed, 112)
        self.assertEqual(args.semantic_per_role, 2500)
        self.assertEqual(args.semantic_epochs, 12)

    def test_predict_requires_sample_identity_and_model(self) -> None:
        args = build_parser().parse_args(
            [
                "predict-result",
                "--corpus",
                "/data/corpus.json",
                "--sample-id",
                "synthetic-000001",
                "--result",
                "/data/result.json",
                "--model",
                "/data/model.json",
            ]
        )
        self.assertEqual(args.sample_id, "synthetic-000001")


if __name__ == "__main__":
    unittest.main()