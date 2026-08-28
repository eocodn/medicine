from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v51_validation_protocol import (
    freeze_parser_v51_candidate,
    load_parser_v51_candidate_freeze,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ParserV51ValidationProtocolTest(unittest.TestCase):
    def test_freeze_binds_training_dev_calibration_and_runtime_sources(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train_manifest = build_parser_v5_dataset(root / "train", dataset_id="v51-freeze-train", document_count=2, seed=91)
            dev_manifest = build_parser_v5_dataset(root / "dev", dataset_id="v51-freeze-dev", document_count=2, seed=92)
            train = load_parser_v5_dataset(train_manifest)
            dev = load_parser_v5_dataset(dev_manifest)
            model_root = root / "model"
            checkpoint = model_root / "checkpoints" / "epoch-0001" / "model.pdparams"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_bytes(b"checkpoint")

            source_root = Path(__file__).resolve().parents[1]
            implementation_names = (
                "parser_v5_model_input.py",
                "parser_v5_document_encoder_paddle.py",
                "parser_v51_targets.py",
                "parser_v51_direct_decoder_paddle.py",
                "parser_v51_loss_paddle.py",
                "parser_v51_model_paddle.py",
                "parser_v51_training_paddle.py",
            )
            implementation = {name: _sha(source_root / name) for name in implementation_names}
            profile = {
                "schema_version": 1,
                "model_id": "parser_v51_direct_rows_v1",
                "train_datasets": [{"dataset_id": train.dataset_id, "samples_sha256": train.samples_sha256}],
                "validation_datasets": [{"dataset_id": dev.dataset_id, "samples_sha256": dev.samples_sha256}],
                "config": {
                    "epochs": 1,
                    "learning_rate": 0.001,
                    "weight_decay": 0.0001,
                    "seed": 1,
                    "max_text_bytes": 96,
                    "hidden_dim": 96,
                    "text_embedding_dim": 32,
                    "text_conv_dim": 48,
                    "layers": 2,
                    "heads": 4,
                    "feedforward_multiplier": 2,
                    "max_rows": 8,
                    "device": "cpu",
                },
                "implementation_sha256": implementation,
            }
            result = {
                "schema_version": 1,
                "status": "ok",
                "profile": profile,
                "profile_sha256": hashlib.sha256(_canonical(profile)).hexdigest(),
                "best_checkpoint": "checkpoints/epoch-0001/model.pdparams",
                "best_checkpoint_sha256": _sha(checkpoint),
                "best_validation": {"validation_loss": 1.0},
            }
            result_path = model_root / "result.json"
            result_path.write_bytes(_canonical(result) + b"\n")
            output = root / "freeze.json"
            calibration = {
                "calibration_fingerprint": "1" * 64,
                "source_fingerprint": "2" * 64,
                "producer_fingerprint": "3" * 64,
            }

            with patch(
                "browser_ocr.document_parsing.parser_v51_validation_protocol.load_parser_v5_calibration",
                return_value=calibration,
            ):
                freeze_parser_v51_candidate(
                    training_result=result_path,
                    development_manifests=[dev_manifest],
                    calibration_artifact=root / "calibration.json",
                    output_path=output,
                )
            frozen = load_parser_v51_candidate_freeze(output)

            self.assertEqual(frozen["checkpoint_sha256"], _sha(checkpoint))
            self.assertEqual(frozen["development_datasets"], profile["validation_datasets"])
            self.assertIn("parser_v51_runtime_decode.py", frozen["implementation_sha256"])
            self.assertIn("parser_v51_inference_paddle.py", frozen["implementation_sha256"])

            corrupted = dict(frozen)
            corrupted["freeze_fingerprint"] = "f" * 64
            output.write_bytes(_canonical(corrupted) + b"\n")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                load_parser_v51_candidate_freeze(output)


if __name__ == "__main__":
    unittest.main()