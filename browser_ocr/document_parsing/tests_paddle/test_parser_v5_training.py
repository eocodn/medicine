from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paddle

from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_training_paddle import (
    ParserV5TrainingConfig,
    train_parser_v5,
)
from browser_ocr.document_parsing.parser_v5_training_cli import build_parser


class ParserV5TrainingTest(unittest.TestCase):
    def setUp(self) -> None:
        paddle.set_device("cpu")

    def _datasets(self, root: Path) -> tuple[Path, Path]:
        train = build_parser_v5_dataset(root / "train", dataset_id="v5-train", document_count=8, seed=624)
        validation = build_parser_v5_dataset(root / "val", dataset_id="v5-val", document_count=5, seed=625)
        return train, validation

    def test_cli_exposes_separate_train_and_validation_manifests(self) -> None:
        args = build_parser().parse_args([
            "--train-manifest", "/tmp/train.json",
            "--validation-manifest", "/tmp/val.json",
            "--output-dir", "/tmp/model",
            "--epochs", "3",
            "--device", "cpu",
        ])
        self.assertEqual(args.train_manifest, ["/tmp/train.json"])
        self.assertEqual(args.validation_manifest, ["/tmp/val.json"])
        self.assertEqual(args.epochs, 3)

    def test_training_writes_hash_bound_best_checkpoint_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, validation = self._datasets(root)
            output = root / "model"
            config = ParserV5TrainingConfig(epochs=2, learning_rate=0.001, seed=19, device="cpu")
            first = train_parser_v5(
                train_manifests=[train],
                validation_manifests=[validation],
                output_dir=output,
                config=config,
            )
            second = train_parser_v5(
                train_manifests=[train],
                validation_manifests=[validation],
                output_dir=output,
                config=config,
            )
            self.assertEqual(first, second)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(len(first["history"]), 2)
            self.assertTrue(Path(first["best_checkpoint"]).is_file())
            self.assertEqual(len(first["best_checkpoint_sha256"]), 64)
            self.assertIn("role_micro_f1", first["best_validation"])
            self.assertIn("assignment_accuracy", first["best_validation"])
            self.assertIn("candidate_accuracy", first["best_validation"])

    def test_training_resumes_from_last_complete_epoch_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, validation = self._datasets(root)
            output = root / "resume"
            config = ParserV5TrainingConfig(epochs=3, learning_rate=0.001, seed=23, device="cpu")
            from browser_ocr.document_parsing import parser_v5_training_paddle as module

            original = module._train_epoch
            calls = {"count": 0}

            def fail_second(*args, **kwargs):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("injected failure")
                return original(*args, **kwargs)

            with patch.object(module, "_train_epoch", fail_second):
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    train_parser_v5(
                        train_manifests=[train], validation_manifests=[validation], output_dir=output, config=config
                    )
            self.assertTrue((output / "checkpoints" / "epoch-0001" / "model.pdparams").is_file())

            resumed = train_parser_v5(
                train_manifests=[train], validation_manifests=[validation], output_dir=output, config=config
            )
            self.assertEqual([item["epoch"] for item in resumed["history"]], [1, 2, 3])

    def test_training_profile_rejects_dataset_or_config_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, validation = self._datasets(root)
            output = root / "model"
            train_parser_v5(
                train_manifests=[train],
                validation_manifests=[validation],
                output_dir=output,
                config=ParserV5TrainingConfig(epochs=1, seed=7, device="cpu"),
            )
            with self.assertRaisesRegex(ValueError, "profile"):
                train_parser_v5(
                    train_manifests=[train],
                    validation_manifests=[validation],
                    output_dir=output,
                    config=ParserV5TrainingConfig(epochs=1, seed=8, device="cpu"),
                )


if __name__ == "__main__":
    unittest.main()