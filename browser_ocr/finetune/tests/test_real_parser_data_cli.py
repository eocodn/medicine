from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from browser_ocr.document_parsing.real_data import REAL_PARSER_LOCK_FILE, annotation_immutable_sha256, load_real_source_manifest, prepare_real_annotation
from browser_ocr.finetune.real_parser_data_cli import _batch_profile, build_parser, run_real_batch


class RealParserDataCliTest(unittest.TestCase):
    def _model_inputs(self, root: Path) -> tuple[Path, Path, Path, Path]:
        checkpoint = root / "best.pdparams"
        checkpoint.write_bytes(b"recognizer-checkpoint")
        (root / "config.yml").write_text("Global: {}\n", encoding="utf-8")
        recognizer_result = root / "recognizer_result.json"
        recognizer_result.write_text(json.dumps({
            "status": "ok",
            "best_checkpoint": str(checkpoint),
            "best_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        detector_manifest = root / "detector-models.json"
        detector_manifest.write_text(json.dumps({
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
        (extracted / "inference.onnx").write_bytes(b"detector-onnx")
        (extracted / "inference.yml").write_text("PostProcess: {}\n", encoding="utf-8")
        paddle_root = root / "PaddleOCR"
        (paddle_root / "tools").mkdir(parents=True)
        (paddle_root / "ppocr" / "utils" / "dict").mkdir(parents=True)
        (paddle_root / "ppocr" / "engine").mkdir(parents=True)
        (paddle_root / "tools" / "infer_rec.py").write_text("# infer fixture\n", encoding="utf-8")
        (paddle_root / "ppocr" / "engine" / "runner.py").write_text("# runner fixture\n", encoding="utf-8")
        (paddle_root / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt").write_text("가\n나\n", encoding="utf-8")
        return recognizer_result, detector_manifest, paddle_root, detector_root

    def _source_manifest(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        image = root / "rx-001.jpg"
        content = b"\xff\xd8\xffdeidentified"
        image.write_bytes(content)
        (root / "samples.jsonl").write_text(json.dumps({
            "document_id": "rx-001",
            "image": image.name,
            "image_sha256": hashlib.sha256(content).hexdigest(),
            "split": "val",
            "document_type": "prescription",
            "layout_family": "real_unknown",
            "privacy": {"contains_patient_data": False, "deidentified": True},
            "provenance": {"source_id": "source-a", "license_id": "private-deidentified"},
            "scenario_tags": ["prescription"],
            "risk_tags": ["real_photo"],
        }) + "\n", encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": "real-fixture",
            "source_kind": "real_deidentified",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
        }), encoding="utf-8")
        return manifest

    def _completed_batch_fixture(self, root: Path) -> tuple[argparse.Namespace, Path]:
        source_manifest = self._source_manifest(root / "source")
        recognizer_result, detector_manifest, paddle_root, detector_root = self._model_inputs(root)
        output = root / "out"
        args = build_parser().parse_args([
            "--source-manifest", str(source_manifest),
            "--recognizer-result", str(recognizer_result),
            "--detector-manifest", str(detector_manifest),
            "--detector-root", str(detector_root),
            "--paddleocr-root", str(paddle_root),
            "--output-dir", str(output),
            "--recognizer-device", "cpu",
        ])
        source = load_real_source_manifest(source_manifest)
        sample = source.samples[0]
        profile = _batch_profile(args, source)
        producer = profile["ocr_producer"]
        runtime_profile = {
            **producer,
            "image_sha256": sample["image_sha256"],
            "implementation": {
                **producer["implementation"],
            },
        }
        runtime_result = {
            "status": "ok",
            "profile": runtime_profile,
            "image": {"sha256": sample["image_sha256"], "width": 1200, "height": 1600, "source_width": 1200, "source_height": 1600},

            "stages": {"orientation": {"applied_rotation_degrees": 0}},
            "regions": [{
                "index": 1,
                "text": "가나다정",
                "recognition_score": 0.98,
                "polygon": [[10, 10], [100, 10], [100, 35], [10, 35]],
            }],
        }
        runtime_path = output / "runtime" / "rx-001" / "result.json"
        runtime_path.parent.mkdir(parents=True)
        runtime_path.write_text(json.dumps(runtime_result), encoding="utf-8")
        annotation = prepare_real_annotation(sample, runtime_result)
        annotation["observation"]["nodes"][0].update(
            label_status="labeled",
            semantic_role="product",
            association_group="m1",
        )
        annotations = output / "annotations"
        annotations.mkdir(parents=True)
        annotation_path = annotations / "rx-001.json"
        annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")
        annotation_index = {
            "schema_version": 3,
            "source_dataset_id": source.dataset_id,
            "source_manifest": str(source.manifest_path),
            "source_manifest_sha256": profile["source_manifest_sha256"],
            "source_samples": str(source.samples_path),
            "source_samples_sha256": profile["source_samples_sha256"],
            "ocr_producer": producer,
            "documents": [{
                "document_id": "rx-001",
                "annotation": "rx-001.json",
                "immutable_sha256": annotation_immutable_sha256(annotation),
                "runtime_result": str(runtime_path),
                "runtime_result_sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            }],
        }
        (annotations / "index.json").write_text(json.dumps(annotation_index), encoding="utf-8")
        output.joinpath("state.json").write_text(json.dumps({
            "schema_version": 1,
            "status": "completed",
            "profile": profile,
            "completed": 1,
        }), encoding="utf-8")
        output.joinpath("result.json").write_text(json.dumps({
            "status": "ok",
            "documents": 1,
            "runtime_root": str(output / "runtime"),
            "annotations_dir": str(annotations),
            "profile": profile,
        }), encoding="utf-8")
        return args, annotation_path

    def test_defaults_match_current_full_document_path(self) -> None:
        args = build_parser().parse_args([
            "--source-manifest", "/real/manifest.json",
            "--recognizer-result", "/run/recognizer_result.json",
            "--output-dir", "/run/real-parser",
        ])
        self.assertEqual(args.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(args.detector_edge, 640)
        self.assertEqual(args.detector_threads, 1)
        self.assertEqual(args.recognizer_device, "gpu")

    def test_real_batch_uses_shared_real_parser_output_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest = self._source_manifest(root / "source")
            recognizer_result, detector_manifest, paddle_root, detector_root = self._model_inputs(root)
            output = root / "out"
            args = build_parser().parse_args([
                "--source-manifest", str(source_manifest),
                "--recognizer-result", str(recognizer_result),
                "--detector-manifest", str(detector_manifest),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle_root),
                "--output-dir", str(output),
                "--recognizer-device", "cpu",
            ])
            captured: list[Path] = []

            @contextmanager
            def stop_after_lock(path: Path):
                captured.append(path)
                raise RuntimeError("captured lock")
                yield

            with patch("browser_ocr.finetune.real_parser_data_cli._exclusive_lock", stop_after_lock):
                with self.assertRaisesRegex(RuntimeError, "captured lock"):
                    run_real_batch(args)
            self.assertEqual(captured, [output.resolve() / REAL_PARSER_LOCK_FILE])

    def test_runtime_receives_the_batch_ocr_configuration_directly(self) -> None:
        args = build_parser().parse_args([
            "--source-manifest", "/real/manifest.json",
            "--recognizer-result", "/run/recognizer_result.json",
            "--output-dir", "/run/real-parser",
            "--recognizer-device", "cpu",
        ])
        self.assertEqual(args.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(args.recognizer_device, "cpu")

    def test_completed_batch_rerun_does_not_overwrite_human_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            args, annotation_path = self._completed_batch_fixture(root)
            with patch("browser_ocr.finetune.real_parser_data_cli.FullDocumentRuntime", side_effect=AssertionError("must not rerun OCR")):
                result = run_real_batch(args)
            self.assertEqual(result["status"], "ok")
            preserved = json.loads(annotation_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["observation"]["nodes"][0]["semantic_role"], "product")

    def test_completed_batch_rejects_missing_annotation_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            args, annotation_path = self._completed_batch_fixture(root)
            (annotation_path.parent / "index.json").unlink()
            with self.assertRaisesRegex(Exception, "annotation index"):
                run_real_batch(args)

    def test_batch_profile_binds_resolved_ocr_producer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest = self._source_manifest(root / "source")
            recognizer_result, detector_manifest, paddle_root, detector_root = self._model_inputs(root)
            args = build_parser().parse_args([
                "--source-manifest", str(source_manifest),
                "--recognizer-result", str(recognizer_result),
                "--detector-manifest", str(detector_manifest),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle_root),
                "--output-dir", str(root / "out"),
                "--recognizer-device", "cpu",
            ])
            source = load_real_source_manifest(source_manifest)
            first = _batch_profile(args, source)
            producer = first["ocr_producer"]
            self.assertEqual(producer["recognizer_checkpoint_sha256"], hashlib.sha256((root / "best.pdparams").read_bytes()).hexdigest())
            self.assertEqual(producer["recognizer_config_sha256"], hashlib.sha256((root / "config.yml").read_bytes()).hexdigest())
            self.assertEqual(producer["detector_asset_sha256"], "d" * 64)
            self.assertIn("full_document", producer["implementation"])

            detector_manifest.write_text(json.dumps({
                "models": {"PP-OCRv5_mobile_det": {
                    "sha256": "e" * 64,
                    "archive_root": "PP-OCRv5_mobile_det_onnx_infer",
                    "onnx_file": "inference.onnx",
                    "config_file": "inference.yml",
                }},
            }), encoding="utf-8")
            second = _batch_profile(args, source)
            self.assertNotEqual(second["ocr_producer"], producer)

            (paddle_root / "ppocr" / "engine" / "runner.py").write_text("# changed runtime source\n", encoding="utf-8")
            third = _batch_profile(args, source)
            self.assertNotEqual(third["ocr_producer"], second["ocr_producer"])

    def test_running_batch_adopts_matching_uncheckpointed_annotation_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest = self._source_manifest(root / "source")
            recognizer_result, detector_manifest, paddle_root, detector_root = self._model_inputs(root)
            output = root / "out"
            args = build_parser().parse_args([
                "--source-manifest", str(source_manifest),
                "--recognizer-result", str(recognizer_result),
                "--detector-manifest", str(detector_manifest),
                "--detector-root", str(detector_root),
                "--paddleocr-root", str(paddle_root),
                "--output-dir", str(output),
                "--recognizer-device", "cpu",
            ])
            source = load_real_source_manifest(source_manifest)
            sample = source.samples[0]
            batch_profile = _batch_profile(args, source)
            producer = batch_profile["ocr_producer"]
            runtime_profile = {
                **producer,
                "image_sha256": sample["image_sha256"],
            }
            runtime_result = {
                "status": "ok",
                "profile": runtime_profile,
                "image": {"sha256": sample["image_sha256"], "width": 1200, "height": 1600, "source_width": 1200, "source_height": 1600},

                "stages": {"orientation": {"applied_rotation_degrees": 0}},
                "regions": [{
                    "index": 1,
                    "text": "가나다정",
                    "recognition_score": 0.98,
                    "polygon": [[10, 10], [100, 10], [100, 35], [10, 35]],
                }],
            }
            result_path = output / "runtime" / "rx-001" / "result.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text(json.dumps(runtime_result), encoding="utf-8")
            annotation = prepare_real_annotation(sample, runtime_result)
            annotation["observation"]["nodes"][0].update(
                label_status="labeled",
                semantic_role="product",
                association_group="m1",
            )
            annotation_path = output / "annotations" / "rx-001.json"
            annotation_path.parent.mkdir(parents=True)
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")
            output.joinpath("state.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "running",
                "profile": batch_profile,
                "completed": 0,
            }), encoding="utf-8")

            with patch("browser_ocr.finetune.real_parser_data_cli.FullDocumentRuntime", side_effect=AssertionError("must adopt existing OCR result")):
                result = run_real_batch(args)
            self.assertEqual(result["status"], "ok")
            preserved = json.loads(annotation_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["observation"]["nodes"][0]["semantic_role"], "product")


if __name__ == "__main__":
    unittest.main()
