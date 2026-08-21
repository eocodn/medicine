from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.full_document_cli import (
    _gpu_runtime_identity,
    _implementation_profile,
    _runtime_environment_sha256,
    build_ocr_producer_profile,
    build_parser,
    load_selected_recognizer,
    run_full_document,
)


class FullDocumentCliContractTest(unittest.TestCase):
    def _producer_inputs(self, root: Path, *, source_marker: str = "v1") -> tuple[Path, Path, Path, Path]:
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
            "models": {"PP-OCRv5_mobile_det": {
                "sha256": "d" * 64,
                "archive_root": "PP-OCRv5_mobile_det_onnx_infer",
                "onnx_file": "inference.onnx",
                "config_file": "inference.yml",
            }},
        }), encoding="utf-8")
        detector_root = root / "detector-cache"
        extracted = detector_root / "PP-OCRv5_mobile_det_onnx_infer"
        extracted.mkdir(parents=True)
        (extracted / "inference.onnx").write_bytes(b"detector-onnx-v1")
        (extracted / "inference.yml").write_text("PostProcess: {}\n", encoding="utf-8")
        paddle = root / "PaddleOCR"
        (paddle / "tools").mkdir(parents=True)
        (paddle / "ppocr" / "utils" / "dict").mkdir(parents=True)
        (paddle / "ppocr" / "engine").mkdir(parents=True)
        (paddle / "tools" / "infer_rec.py").write_text(f"# infer {source_marker}\n", encoding="utf-8")
        (paddle / "ppocr" / "engine" / "runner.py").write_text(f"# runner {source_marker}\n", encoding="utf-8")
        (paddle / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt").write_text("가\n나\n", encoding="utf-8")
        return baseline, detector, paddle, detector_root

    def test_output_profile_pins_ocr_pipeline_implementation(self) -> None:
        profile = _implementation_profile()
        self.assertEqual(
            set(profile),
            {
                "full_document",
                "full_document_cli",
                "crop_refinement",
                "detector_runtime",
                "detector_benchmark",
            },
        )
        self.assertTrue(all(len(value) == 64 for value in profile.values()))

    def test_full_document_ocr_defaults_to_selected_mobile_detector(self) -> None:
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
            baseline, detector, paddle, detector_root = self._producer_inputs(root)
            args = build_parser().parse_args([
                "--image", str(root / "unused.jpg"),
                "--baseline-result", str(baseline),
                "--output-dir", str(root / "out"),
                "--detector-manifest", str(detector),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle),
                "--recognizer-device", "cpu",
            ])
            first = build_ocr_producer_profile(args)
            self.assertRegex(first["paddleocr_source_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(first["paddleocr_dictionary_sha256"], r"^[0-9a-f]{64}$")

            (paddle / "ppocr" / "engine" / "runner.py").write_text("# modified runtime\n", encoding="utf-8")
            second = build_ocr_producer_profile(args)
            self.assertNotEqual(second["paddleocr_source_sha256"], first["paddleocr_source_sha256"])

            self.assertRegex(first["inference_runtime_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(first["detector_onnx_sha256"], hashlib.sha256(b"detector-onnx-v1").hexdigest())
            onnx = detector_root / "PP-OCRv5_mobile_det_onnx_infer" / "inference.onnx"
            onnx.write_bytes(b"detector-onnx-v2")
            third = build_ocr_producer_profile(args)
            self.assertNotEqual(third["detector_onnx_sha256"], first["detector_onnx_sha256"])

    def test_ocr_producer_profile_changes_when_inference_runtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline, detector, paddle, detector_root = self._producer_inputs(root)
            args = build_parser().parse_args([
                "--image", str(root / "unused.jpg"),
                "--baseline-result", str(baseline),
                "--output-dir", str(root / "out"),
                "--detector-manifest", str(detector),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle),
                "--recognizer-device", "cpu",
            ])
            with patch("browser_ocr.finetune.full_document_cli._runtime_environment_sha256", return_value="a" * 64):
                first = build_ocr_producer_profile(args)
            with patch("browser_ocr.finetune.full_document_cli._runtime_environment_sha256", return_value="b" * 64):
                second = build_ocr_producer_profile(args)
            self.assertEqual(first["inference_runtime_sha256"], "a" * 64)
            self.assertEqual(second["inference_runtime_sha256"], "b" * 64)
            self.assertNotEqual(first, second)

    def test_runtime_environment_hash_binds_native_runtime_identity(self) -> None:
        with patch(
            "browser_ocr.finetune.full_document_cli._native_runtime_identity",
            return_value={"packages": ["libgomp1=1"], "libraries": {"libgomp.so.1": "a" * 64}},
            create=True,
        ):
            first = _runtime_environment_sha256("cpu")
        with patch(
            "browser_ocr.finetune.full_document_cli._native_runtime_identity",
            return_value={"packages": ["libgomp1=1"], "libraries": {"libgomp.so.1": "b" * 64}},
            create=True,
        ):
            second = _runtime_environment_sha256("cpu")
        self.assertNotEqual(first, second)

    def test_runtime_environment_hash_binds_python_wheel_native_payloads(self) -> None:
        with patch(
            "browser_ocr.finetune.full_document_cli._python_native_runtime_identity",
            return_value={"onnxruntime": {"onnxruntime/capi/libonnxruntime.so": "a" * 64}},
            create=True,
        ):
            first = _runtime_environment_sha256("cpu")
        with patch(
            "browser_ocr.finetune.full_document_cli._python_native_runtime_identity",
            return_value={"onnxruntime": {"onnxruntime/capi/libonnxruntime.so": "b" * 64}},
            create=True,
        ):
            second = _runtime_environment_sha256("cpu")
        self.assertNotEqual(first, second)

    def test_ocr_producer_profile_binds_dictionary_selected_by_recognizer_config(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline, detector, paddle, detector_root = self._producer_inputs(root)
            selected_dictionary = root / "selected-dict.txt"
            selected_dictionary.write_text("가\n나\n", encoding="utf-8")
            config = root / "model" / "config.yml"
            config.write_text(
                f"Global:\n  character_dict_path: {selected_dictionary}\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args([
                "--image", str(root / "unused.jpg"),
                "--baseline-result", str(baseline),
                "--output-dir", str(root / "out"),
                "--detector-manifest", str(detector),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle),
                "--recognizer-device", "cpu",
            ])
            first = build_ocr_producer_profile(args)
            selected_dictionary.write_text("가\n나\n다\n", encoding="utf-8")
            second = build_ocr_producer_profile(args)
            self.assertNotEqual(first["paddleocr_dictionary_sha256"], second["paddleocr_dictionary_sha256"])
            self.assertNotEqual(first, second)

    def test_gpu_producer_profile_binds_runtime_selected_device(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            baseline, detector, paddle, detector_root = self._producer_inputs(root)
            args = build_parser().parse_args([
                "--image", str(root / "unused.jpg"),
                "--baseline-result", str(baseline),
                "--output-dir", str(root / "out"),
                "--detector-manifest", str(detector),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle),
                "--recognizer-device", "gpu",
            ])
            runtime = {
                "paddle_version": "3.2.0",
                "device_name": "Fixture GPU",
                "compute_capability": [8, 9],
                "cuda_version": "12.6",
                "cudnn_version": 90501,
            }
            with patch(
                "browser_ocr.finetune.full_document_cli._gpu_runtime_identity",
                return_value=runtime,
                create=True,
            ):
                with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0"}, clear=False):
                    first = build_ocr_producer_profile(args)
                with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "1"}, clear=False):
                    second = build_ocr_producer_profile(args)
            self.assertNotEqual(first["inference_runtime_sha256"], second["inference_runtime_sha256"])
            self.assertNotEqual(first, second)

    def test_gpu_runtime_identity_binds_nvidia_driver_report(self) -> None:
        fake_paddle = SimpleNamespace()
        nvidia = SimpleNamespace(
            stdout="GPU-fixture, Fixture GPU, 610.47, 8.9\n",
        )
        with patch.dict(sys.modules, {"paddle": fake_paddle}), \
             patch(
                 "browser_ocr.finetune.training.probe_paddle_runtime",
                 return_value={
                     "schema_version": 1,
                     "status": "ok",
                     "paddle_version": "3.2.0",
                     "device_name": "Fixture GPU",
                     "compute_capability": [8, 9],
                     "cuda_version": "12.6",
                     "cudnn_version": 90501,
                 },
             ), \
             patch("browser_ocr.finetune.full_document_cli.subprocess.run", return_value=nvidia):
            identity = _gpu_runtime_identity()
        self.assertEqual(identity["nvidia_smi"], ["GPU-fixture, Fixture GPU, 610.47, 8.9"])

    def test_completed_full_document_rejects_mutated_result_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "doc.jpg"
            image.write_bytes(b"fixture-image")
            output = root / "out"
            output.mkdir()
            profile = {"fixture": "profile"}
            original = {"status": "ok", "profile": profile, "regions": [{"text": "ORIGINAL"}]}
            result_path = output / "result.json"
            result_path.write_text(json.dumps(original, sort_keys=True), encoding="utf-8")
            digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
            (output / "state.json").write_text(json.dumps({
                "schema_version": 2, "status": "completed", "profile": profile, "result_sha256": digest,
            }), encoding="utf-8")
            forged = {**original, "regions": [{"text": "FORGED OCR"}]}
            result_path.write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
            args = build_parser().parse_args([
                "--image", str(image), "--baseline-result", str(root / "unused.json"), "--output-dir", str(output),
            ])
            with patch("browser_ocr.finetune.full_document_cli.load_selected_recognizer", return_value={}), \
                 patch("browser_ocr.finetune.full_document_cli._profile", return_value=profile):
                with self.assertRaisesRegex(DatasetError, "result.*SHA-256|completed.*result"):
                    run_full_document(args)


if __name__ == "__main__":
    unittest.main()
