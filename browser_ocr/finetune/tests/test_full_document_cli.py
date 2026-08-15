from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.full_document_cli import build_parser, load_selected_recognizer


class FullDocumentCliContractTest(unittest.TestCase):
    def test_parser_defaults_to_selected_mobile_detector(self) -> None:
        args = build_parser().parse_args([
            "--image", "/data/doc.jpg",
            "--baseline-result", "/run/baseline-result.json",
            "--output-dir", "/run/full-doc",
        ])
        self.assertEqual(args.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(args.detector_edge, 640)
        self.assertEqual(args.detector_threads, 1)

    def test_selected_recognizer_is_bound_to_baseline_hash_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            model = root / "model"
            model.mkdir()
            checkpoint = model / "best_accuracy.pdparams"
            checkpoint.write_bytes(b"selected-model")
            (model / "config.yml").write_text("Global: {}\n", encoding="utf-8")
            digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            result = root / "baseline-result.json"
            result.write_text(json.dumps({
                "status": "ok",
                "best_checkpoint": str(checkpoint),
                "best_checkpoint_sha256": digest,
                "best_test": {"acc": 0.9987},
            }), encoding="utf-8")

            selected = load_selected_recognizer(result)
            self.assertEqual(selected["checkpoint"], checkpoint)
            self.assertEqual(selected["checkpoint_sha256"], digest)
            self.assertEqual(selected["config"], model / "config.yml")

            checkpoint.write_bytes(b"mutated")
            with self.assertRaisesRegex(DatasetError, "SHA-256 mismatch"):
                load_selected_recognizer(result)


if __name__ == "__main__":
    unittest.main()
