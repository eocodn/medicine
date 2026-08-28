from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import paddle

from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset
from browser_ocr.document_parsing.parser_v51_training_cli import build_parser
from browser_ocr.document_parsing.parser_v51_training_paddle import (
    ParserV51TrainingConfig,
    train_parser_v51,
)


class ParserV51TrainingTest(unittest.TestCase):
    def setUp(self) -> None:
        paddle.set_device("cpu")

    def _datasets(self, root: Path) -> tuple[Path, Path]:
        train = build_parser_v5_dataset(root / "train", dataset_id="v51-train", document_count=6, seed=751)
        validation = build_parser_v5_dataset(root / "val", dataset_id="v51-val", document_count=4, seed=752)
        return train, validation

    def test_cli_uses_direct_v51_training_surface(self) -> None:
        args = build_parser().parse_args([
            "--train-manifest", "/tmp/train.json",
            "--validation-manifest", "/tmp/val.json",
            "--output-dir", "/tmp/model",
            "--epochs", "2",
            "--device", "cpu",
        ])
        self.assertEqual(args.train_manifest, ["/tmp/train.json"])
        self.assertEqual(args.validation_manifest, ["/tmp/val.json"])
        self.assertEqual(args.epochs, 2)

    def test_training_writes_direct_metrics_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, validation = self._datasets(root)
            output = root / "model"
            config = ParserV51TrainingConfig(
                epochs=2,
                learning_rate=0.001,
                seed=753,
                hidden_dim=48,
                text_embedding_dim=16,
                text_conv_dim=24,
                layers=1,
                heads=4,
                max_rows=8,
                device="cpu",
            )
            first = train_parser_v51(
                train_manifests=[train],
                validation_manifests=[validation],
                output_dir=output,
                config=config,
            )
            second = train_parser_v51(
                train_manifests=[train],
                validation_manifests=[validation],
                output_dir=output,
                config=config,
            )

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["profile"]["model_id"], "parser_v51_direct_rows_v1")
            self.assertEqual(len(first["history"]), 2)
            self.assertTrue((output / first["best_checkpoint"]).is_file())
            metrics = first["best_validation"]
            self.assertIn("validation_loss", metrics)
            self.assertIn("row_existence_accuracy", metrics)
            self.assertIn("field_presence_accuracy", metrics)
            self.assertIn("node_membership_accuracy", metrics)
            self.assertIn("span_exact_rate", metrics)
            self.assertNotIn("role_micro_f1", metrics)
            self.assertNotIn("candidate_accuracy", metrics)

    def test_training_rejects_same_samples_for_train_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = build_parser_v5_dataset(root / "same", dataset_id="v51-same", document_count=2, seed=754)
            with self.assertRaisesRegex(ValueError, "disjoint"):
                train_parser_v51(
                    train_manifests=[manifest],
                    validation_manifests=[manifest],
                    output_dir=root / "model",
                    config=ParserV51TrainingConfig(epochs=1, device="cpu"),
                )


if __name__ == "__main__":
    unittest.main()