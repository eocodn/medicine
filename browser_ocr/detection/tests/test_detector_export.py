from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from browser_ocr.detection.export_stage import (
    DetectorExportError,
    prepare_paddle_export,
    run_paddle_export,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path) -> dict[str, Path]:
    paddleocr = root / "PaddleOCR"
    (paddleocr / "tools").mkdir(parents=True)
    (paddleocr / "tools/export_model.py").write_text("# fixture\n", encoding="utf-8")

    model_dir = root / "training/model"
    model_dir.mkdir(parents=True)
    checkpoint = model_dir / "best_accuracy.pdparams"
    checkpoint.write_bytes(b"trained-detector-checkpoint")
    trained_config = model_dir / "config.yml"
    trained_config.write_text("Global:\n  model_name: PP-OCRv5_mobile_det\n", encoding="utf-8")

    training_result = root / "training/result.json"
    _write_json(
        training_result,
        {
            "schema_version": 1,
            "status": "ok",
            "profile": {
                "schema_version": 1,
                "runner": "ppocrv5-mobile-document-detector-finetune-v1",
                "paddleocr_commit": "b03f46425e8ff4442b268ce449e3eef758146cd4",
                "corpus": {
                    "id": "synthetic-v6-fixture",
                    "manifest_sha256": "1" * 64,
                },
            },
            "best_checkpoint": str(checkpoint),
            "best_checkpoint_sha256": _sha(checkpoint),
            "trained_config": str(trained_config),
            "trained_config_sha256": _sha(trained_config),
            "final_epoch_checkpoint": str(model_dir / "iter_epoch_6"),
            "promotion_status": "pending_project_safety_evaluation",
        },
    )
    return {
        "paddleocr": paddleocr,
        "checkpoint": checkpoint,
        "trained_config": trained_config,
        "training_result": training_result,
    }


class DetectorPaddleExportTest(unittest.TestCase):
    def test_preflight_binds_training_result_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate-stage"
            result = prepare_paddle_export(
                training_result=paths["training_result"],
                paddleocr_root=paths["paddleocr"],
                output_dir=output,
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["profile"]["training_result_sha256"], _sha(paths["training_result"]))
            self.assertEqual(result["profile"]["checkpoint_sha256"], _sha(paths["checkpoint"]))
            command = " ".join(result["command"])
            self.assertIn("tools/export_model.py", command)
            self.assertIn(f"Global.checkpoints={paths['checkpoint'].with_suffix('')}", command)
            self.assertIn(f"Global.save_inference_dir={output / 'paddle'}", command)
            self.assertNotIn("Global.pretrained_model", command)

    def test_preflight_rejects_tampered_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            paths["checkpoint"].write_bytes(b"tampered")
            with self.assertRaisesRegex(DetectorExportError, "checkpoint SHA-256"):
                prepare_paddle_export(
                    training_result=paths["training_result"],
                    paddleocr_root=paths["paddleocr"],
                    output_dir=Path(raw) / "candidate-stage",
                )

    def test_run_is_idempotent_and_hashes_paddle_inference_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate-stage"

            def fake_export(command: list[str], *, cwd: Path, log_path: Path) -> None:
                self.assertEqual(cwd, paths["paddleocr"])
                self.assertTrue(any("Global.checkpoints=" in item for item in command))
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("exported\n", encoding="utf-8")
                paddle = output / "paddle"
                paddle.mkdir(parents=True, exist_ok=True)
                (paddle / "inference.json").write_bytes(b"paddle-program")
                (paddle / "inference.pdiparams").write_bytes(b"paddle-params")
                (paddle / "inference.yml").write_text(
                    "Global:\n  model_name: PP-OCRv5_mobile_det\n",
                    encoding="utf-8",
                )

            with patch("browser_ocr.detection.export_stage._stream_export", side_effect=fake_export):
                result = run_paddle_export(
                    training_result=paths["training_result"],
                    paddleocr_root=paths["paddleocr"],
                    output_dir=output,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["promotion_status"], "pending_onnx_conversion_and_safety_evaluation")
            self.assertEqual(result["files"]["inference.json"]["sha256"], _sha(output / "paddle/inference.json"))
            self.assertEqual(result["files"]["inference.pdiparams"]["sha256"], _sha(output / "paddle/inference.pdiparams"))
            self.assertEqual(result["files"]["inference.yml"]["sha256"], _sha(output / "paddle/inference.yml"))

            again = run_paddle_export(
                training_result=paths["training_result"],
                paddleocr_root=paths["paddleocr"],
                output_dir=output,
            )
            self.assertEqual(again, result)

    def test_agent_control_cli_reports_export_preflight_as_json(self) -> None:
        from browser_ocr.detection.export_cli import main

        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate-stage"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "preflight",
                        "--training-result",
                        str(paths["training_result"]),
                        "--paddleocr-root",
                        str(paths["paddleocr"]),
                        "--output-dir",
                        str(output),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["profile"]["checkpoint_sha256"], _sha(paths["checkpoint"]))


if __name__ == "__main__":
    unittest.main()