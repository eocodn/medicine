from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from browser_ocr.detection.training import (
    DetectorTrainingConfig,
    DetectorTrainingError,
    prepare_detector_training,
    run_detector_training,
)


ROOT = Path(__file__).resolve().parents[3]
DETECTION = ROOT / "browser_ocr" / "detection"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path) -> dict[str, Path]:
    paddleocr = root / "PaddleOCR"
    official_config = paddleocr / "configs/det/PP-OCRv5/PP-OCRv5_mobile_det.yml"
    official_config.parent.mkdir(parents=True)
    official_config.write_text("official-mobile-det-config\n", encoding="utf-8")
    random_crop = paddleocr / "ppocr/data/imaug/random_crop_data.py"
    random_crop.parent.mkdir(parents=True)
    random_crop.write_text("# numpy-2-compatible-random-crop\n", encoding="utf-8")
    (paddleocr / "tools").mkdir()
    (paddleocr / "tools/train.py").write_text("# fixture\n", encoding="utf-8")

    document_config = root / "PP-OCRv5_mobile_det_document.yml"
    document_config.write_text(
        """Global:
  epoch_num: 500
Train:
  dataset:
    transforms:
    - DecodeImage: null
    - MakeBorderMap:
        shrink_ratio: 0.4
        total_epoch: 500
    - MakeShrinkMap:
        shrink_ratio: 0.4
        min_text_size: 8
        total_epoch: 500
""",
        encoding="utf-8",
    )
    pretrained = root / "PPLCNetV3_x0_75_ocr_det.pdparams"
    pretrained.write_bytes(b"detector-pretrained-v1")

    upstream = root / "training-upstream.json"
    _write_json(upstream, {
        "schema_version": 1,
        "framework": "PaddleOCR",
        "detector": "PP-OCRv5_mobile_det",
        "paddleocr": {
            "commit": "b03f46425e8ff4442b268ce449e3eef758146cd4",
            "config_path": "configs/det/PP-OCRv5/PP-OCRv5_mobile_det.yml",
            "config_sha256": _sha(official_config),
            "runtime_source_files": [
                {
                    "path": "ppocr/data/imaug/random_crop_data.py",
                    "sha256": _sha(random_crop),
                }
            ],
        },
        "document_config": {
            "path": str(document_config),
            "sha256": _sha(document_config),
        },
        "pretrained_model_url": "https://example.invalid/detector.pdparams",
        "pretrained_model_bytes": pretrained.stat().st_size,
        "pretrained_model_sha256": _sha(pretrained),
        "paddlepaddle": {"version": "3.2.0", "cuda_runtime": "12.6"},
        "training_enabled": True,
    })

    corpus = root / "corpus"
    corpus.mkdir()
    images = corpus / "images"
    images.mkdir()
    image_sha: dict[str, str] = {}
    for name in ("train-1", "train-2", "val-1", "test-1"):
        image = images / f"{name}.jpg"
        image.write_bytes(f"fixture-{name}".encode())
        image_sha[name] = _sha(image)
    corpus_manifest = corpus / "manifest.json"
    _write_json(corpus_manifest, {
        "schema_version": 3,
        "synthetic_only": True,
        "corpus_id": "detector-training-fixture",
        "generator": {"id": "medicine_full_document_synthetic", "version": 6, "revision": 2},
        "samples": [
            {"id": "train-1", "split": "train", "image": "images/train-1.jpg", "image_sha256": image_sha["train-1"]},
            {"id": "train-2", "split": "train", "image": "images/train-2.jpg", "image_sha256": image_sha["train-2"]},
            {"id": "val-1", "split": "val", "image": "images/val-1.jpg", "image_sha256": image_sha["val-1"]},
            {"id": "test-1", "split": "test", "image": "images/test-1.jpg", "image_sha256": image_sha["test-1"]},
        ],
    })
    paddle = corpus / "views/detection/paddle"
    paddle.mkdir(parents=True)
    (paddle / "train.txt").write_text("images/train-1.jpg\t[]\nimages/train-2.jpg\t[]\n", encoding="utf-8")
    (paddle / "val.txt").write_text("images/val-1.jpg\t[]\n", encoding="utf-8")
    (paddle / "test.txt").write_text("images/test-1.jpg\t[]\n", encoding="utf-8")
    detection_export = paddle / "export.json"
    _write_json(detection_export, {
        "schema_version": 1,
        "task": "text_detection",
        "parent_corpus_id": "detector-training-fixture",
        "parent_corpus_sha256": _sha(corpus_manifest),
        "data_dir": str(corpus),
        "label_files": {"train": "train.txt", "val": "val.txt", "test": "test.txt"},
        "counts": {"train": 2, "val": 1, "test": 1},
        "polygon_kind": "region_polygon",
        "transcription_policy": "ground_truth_text",
    })
    return {
        "paddleocr": paddleocr,
        "random_crop": random_crop,
        "document_config": document_config,
        "pretrained": pretrained,
        "upstream": upstream,
        "corpus_manifest": corpus_manifest,
        "detection_export": detection_export,
    }


class DetectorTrainingTest(unittest.TestCase):
    def test_checked_in_training_contract_pins_document_safe_augmentation(self) -> None:
        upstream = json.loads((DETECTION / "training-upstream.json").read_text(encoding="utf-8"))
        document = DETECTION / upstream["document_config"]["path"]
        self.assertEqual(_sha(document), upstream["document_config"]["sha256"])
        self.assertEqual(
            upstream["pretrained_model_sha256"],
            "92825c8c7b590c8d7ee4b782a431def0e9b77f9ff0d8ac2a24a5af1a40497607",
        )
        self.assertEqual(upstream["pretrained_model_bytes"], 20103839)
        config = yaml.safe_load(document.read_text(encoding="utf-8"))
        transforms = config["Train"]["dataset"]["transforms"]
        encoded = json.dumps(transforms, ensure_ascii=False)
        self.assertNotIn("Fliplr", encoded)
        self.assertNotIn("CopyPaste", encoded)
        self.assertIn("EastRandomCropData", encoded)
        transform_names = [next(iter(item)) for item in transforms]
        self.assertIn("DetLabelEncode", transform_names)
        self.assertLess(transform_names.index("DetLabelEncode"), transform_names.index("IaaAugment"))
        crop = next(item["EastRandomCropData"] for item in transforms if "EastRandomCropData" in item)
        self.assertEqual(crop["size"], [960, 960])
        self.assertEqual(crop["max_tries"], 0)
        self.assertTrue(crop["keep_ratio"])
        self.assertEqual(config["Global"]["d2s_train_image_shape"], [3, 960, 960])

    def test_preflight_is_hash_bound_and_never_uses_test_labels_for_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            run_dir = Path(raw) / "run"
            result = prepare_detector_training(
                upstream_path=paths["upstream"],
                paddleocr_root=paths["paddleocr"],
                pretrained_model=paths["pretrained"],
                corpus_manifest=paths["corpus_manifest"],
                detection_export=paths["detection_export"],
                run_dir=run_dir,
                config=DetectorTrainingConfig(epochs=6, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
            )

            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["profile"]["corpus"]["counts"], {"train": 2, "val": 1, "test": 1})
            self.assertEqual(result["profile"]["pretrained_model_sha256"], _sha(paths["pretrained"]))
            self.assertEqual(
                result["profile"]["paddleocr_runtime_source_files"],
                [{"path": "ppocr/data/imaug/random_crop_data.py", "sha256": _sha(paths["random_crop"])}],
            )
            command = " ".join(result["command"])
            self.assertIn("Train.dataset.label_file_list", command)
            self.assertIn("Eval.dataset.label_file_list", command)
            self.assertIn("train.txt", command)
            self.assertIn("val.txt", command)
            self.assertNotIn("test.txt", command)
            self.assertIn("Global.epoch_num=6", command)
            self.assertIn("Optimizer.lr.learning_rate=0.0001", command)
            self.assertIn("Global.cal_metric_during_train=False", command)
            self.assertIn("Global.eval_batch_step=[0,1]", command)
            self.assertIn("Global.eval_batch_epoch=1", command)
            transform_override = next(
                item for item in result["command"] if item.startswith("Train.dataset.transforms=")
            )
            transforms = json.loads(transform_override.split("=", 1)[1])
            border = next(item["MakeBorderMap"] for item in transforms if "MakeBorderMap" in item)
            shrink = next(item["MakeShrinkMap"] for item in transforms if "MakeShrinkMap" in item)
            self.assertEqual(border["total_epoch"], 6)
            self.assertEqual(shrink["total_epoch"], 6)
            self.assertEqual(result["promotion"], "requires_project_safety_evaluation")

    def test_preflight_rejects_mutated_paddleocr_runtime_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            paths["random_crop"].write_text("# mutated\n", encoding="utf-8")
            with self.assertRaisesRegex(DetectorTrainingError, "runtime source"):
                prepare_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=Path(raw) / "run",
                    config=DetectorTrainingConfig(),
                )

    def test_preflight_rejects_export_from_another_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            export = json.loads(paths["detection_export"].read_text(encoding="utf-8"))
            export["parent_corpus_sha256"] = "0" * 64
            _write_json(paths["detection_export"], export)
            with self.assertRaisesRegex(DetectorTrainingError, "corpus SHA-256"):
                prepare_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=Path(raw) / "run",
                    config=DetectorTrainingConfig(),
                )

    def test_preflight_rejects_same_count_labels_with_wrong_split_membership(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            train = paths["detection_export"].parent / "train.txt"
            train.write_text("images/train-1.jpg\t[]\nimages/test-1.jpg\t[]\n", encoding="utf-8")
            with self.assertRaisesRegex(DetectorTrainingError, "membership"):
                prepare_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=Path(raw) / "run",
                    config=DetectorTrainingConfig(),
                )

    def test_agent_control_cli_reports_preflight_as_json(self) -> None:
        from browser_ocr.detection.training_cli import main

        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "preflight",
                    "--upstream", str(paths["upstream"]),
                    "--paddleocr-root", str(paths["paddleocr"]),
                    "--pretrained-model", str(paths["pretrained"]),
                    "--corpus-manifest", str(paths["corpus_manifest"]),
                    "--detection-export", str(paths["detection_export"]),
                    "--run-dir", str(Path(raw) / "run"),
                    "--epochs", "3",
                    "--batch-size", "4",
                    "--learning-rate", "0.0001",
                    "--warmup-epochs", "1",
                    "--json",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["profile"]["optimization_splits"], ["train", "val"])

    def test_training_is_resumable_and_completion_does_not_claim_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            run_dir = Path(raw) / "run"

            def fake_training(command: list[str], *, cwd: Path, log_path: Path, on_progress) -> None:
                del cwd
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("epoch 1\n", encoding="utf-8")
                on_progress(1, "epoch 1")
                model_dir = run_dir / "model"
                model_dir.mkdir(parents=True, exist_ok=True)
                for suffix in (".pdparams", ".pdopt", ".states"):
                    (model_dir / f"iter_epoch_3{suffix}").write_bytes(f"epoch3{suffix}".encode())
                (model_dir / "best_accuracy.pdparams").write_bytes(b"best-detector")
                (model_dir / "config.yml").write_text("trained-detector-config\n", encoding="utf-8")
                self.assertFalse(any("test.txt" in item for item in command))

            with patch("browser_ocr.detection.training._stream_training", side_effect=fake_training):
                result = run_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=run_dir,
                    config=DetectorTrainingConfig(epochs=3, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["promotion_status"], "pending_project_safety_evaluation")
            self.assertEqual(result["best_checkpoint_sha256"], _sha(run_dir / "model/best_accuracy.pdparams"))
            state = json.loads((run_dir / "training-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["result_sha256"], _sha(run_dir / "result.json"))

            # An intact completed run is idempotent and cannot silently change hyperparameters.
            again = run_detector_training(
                upstream_path=paths["upstream"],
                paddleocr_root=paths["paddleocr"],
                pretrained_model=paths["pretrained"],
                corpus_manifest=paths["corpus_manifest"],
                detection_export=paths["detection_export"],
                run_dir=run_dir,
                config=DetectorTrainingConfig(epochs=3, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
            )
            self.assertEqual(again, result)
            with self.assertRaisesRegex(DetectorTrainingError, "profile"):
                run_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=run_dir,
                    config=DetectorTrainingConfig(epochs=4, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
                )

    def test_failed_run_resumes_from_latest_complete_epoch_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            run_dir = Path(raw) / "run"
            attempts = 0

            def fake_training(command: list[str], *, cwd: Path, log_path: Path, on_progress) -> None:
                nonlocal attempts
                del cwd
                attempts += 1
                log_path.parent.mkdir(parents=True, exist_ok=True)
                model_dir = run_dir / "model"
                model_dir.mkdir(parents=True, exist_ok=True)
                if attempts == 1:
                    on_progress(1, "epoch 1")
                    for suffix in (".pdparams", ".pdopt", ".states"):
                        (model_dir / f"iter_epoch_1{suffix}").write_bytes(f"epoch1{suffix}".encode())
                    raise DetectorTrainingError("simulated interruption")

                self.assertTrue(any("Global.checkpoints=" in item and "iter_epoch_1" in item for item in command))
                self.assertFalse(any("Global.pretrained_model=" in item for item in command))
                for suffix in (".pdparams", ".pdopt", ".states"):
                    (model_dir / f"iter_epoch_3{suffix}").write_bytes(f"epoch3{suffix}".encode())
                (model_dir / "best_accuracy.pdparams").write_bytes(b"best-detector")
                (model_dir / "config.yml").write_text("trained-detector-config\n", encoding="utf-8")

            with patch("browser_ocr.detection.training._stream_training", side_effect=fake_training):
                with self.assertRaisesRegex(DetectorTrainingError, "simulated interruption"):
                    run_detector_training(
                        upstream_path=paths["upstream"],
                        paddleocr_root=paths["paddleocr"],
                        pretrained_model=paths["pretrained"],
                        corpus_manifest=paths["corpus_manifest"],
                        detection_export=paths["detection_export"],
                        run_dir=run_dir,
                        config=DetectorTrainingConfig(epochs=3, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
                    )
                failed = json.loads((run_dir / "training-state.json").read_text(encoding="utf-8"))
                self.assertEqual(failed["status"], "failed")
                self.assertTrue(failed["recoverable_checkpoint"].endswith("iter_epoch_1"))

                result = run_detector_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddleocr"],
                    pretrained_model=paths["pretrained"],
                    corpus_manifest=paths["corpus_manifest"],
                    detection_export=paths["detection_export"],
                    run_dir=run_dir,
                    config=DetectorTrainingConfig(epochs=3, batch_size=4, learning_rate=1e-4, warmup_epochs=1),
                )
            self.assertEqual(attempts, 2)
            self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()