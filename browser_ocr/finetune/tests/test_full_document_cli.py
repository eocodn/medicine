from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.full_document_cli import (
    _implementation_profile,
    build_ocr_producer_profile,
    build_parser,
    load_selected_recognizer,
)


class FullDocumentCliContractTest(unittest.TestCase):
    def _producer_inputs(self, root: Path, *, source_marker: str = "v1") -> tuple[Path, Path, Path]:
        model = root / "model"
        model.mkdir(parents=True)
        checkpoint = model / "best_accuracy.pdparams"
        checkpoint.write_bytes(b"selected-model")
        (model / "config.yml").write_text("Global: {}\n", encoding="utf-8")
        baseline = root / "baseline-result.json"
        baseline.write_text(json.dumps({
            "status": "ok",
            "best_checkpoint": str(checkpoint),
            "best_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        detector = root / "detector-models.json"
        detector.write_text(json.dumps({
            "models": {"PP-OCRv5_mobile_det": {"sha256": "d" * 64}},
        }), encoding="utf-8")
        paddle = root / "PaddleOCR"
        (paddle / "tools").mkdir(parents=True)
        (paddle / "ppocr" / "utils" / "dict").mkdir(parents=True)
        (paddle / "ppocr" / "engine").mkdir(parents=True)
        (paddle / "tools" / "infer_rec.py").write_text(f"# infer {source_marker}\n", encoding="utf-8")
        (paddle / "ppocr" / "engine" / "runner.py").write_text(f"# runner {source_marker}\n", encoding="utf-8")
        (paddle / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt").write_text("가\n나\n", encoding="utf-8")
        return baseline, detector, paddle

    def test_output_profile_pins_pipeline_and_parser_implementation(self) -> None:
        profile = _implementation_profile()
        self.assertEqual(
            set(profile),
            {
                "full_document",
                "full_document_cli",
                "crop_refinement",
                "parser",
                "parser_contract",
                "detector_runtime",
                "detector_benchmark",
            },
        )
        self.assertTrue(all(len(value) == 64 for value in profile.values()))

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

    def test_ocr_producer_profile_binds_actual_paddleocr_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline, detector, paddle = self._producer_inputs(root)
            args = build_parser().parse_args([
                "--image", str(root / "unused.jpg"),
                "--baseline-result", str(baseline),
                "--output-dir", str(root / "out"),
                "--detector-manifest", str(detector),
                "--paddleocr-root", str(paddle),
                "--recognizer-device", "cpu",
            ])
            first = build_ocr_producer_profile(args)
            self.assertRegex(first["paddleocr_source_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(first["paddleocr_dictionary_sha256"], r"^[0-9a-f]{64}$")

            (paddle / "ppocr" / "engine" / "runner.py").write_text("# modified runtime\n", encoding="utf-8")
            second = build_ocr_producer_profile(args)
            self.assertNotEqual(second["paddleocr_source_sha256"], first["paddleocr_source_sha256"])


if __name__ == "__main__":
    unittest.main()
