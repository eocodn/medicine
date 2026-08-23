from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from browser_ocr.detection.candidate_convert import (
    CandidateConversionError,
    prepare_candidate_conversion,
    run_candidate_conversion,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path) -> dict[str, Path]:
    stage = root / "stage"
    paddle = stage / "paddle"
    paddle.mkdir(parents=True)
    (paddle / "inference.json").write_bytes(b"paddle-program")
    (paddle / "inference.pdiparams").write_bytes(b"paddle-params")
    (paddle / "inference.yml").write_text(
        "Global:\n"
        "  model_name: PP-OCRv5_mobile_det\n"
        "PreProcess:\n"
        "  transform_ops:\n"
        "  - DecodeImage:\n"
        "      channel_first: false\n"
        "      img_mode: BGR\n"
        "  - NormalizeImage:\n"
        "      mean: [0.485, 0.456, 0.406]\n"
        "      order: hwc\n"
        "      scale: 1./255.\n"
        "      std: [0.229, 0.224, 0.225]\n"
        "PostProcess:\n"
        "  name: DBPostProcess\n"
        "  thresh: 0.3\n"
        "  box_thresh: 0.6\n"
        "  max_candidates: 1000\n"
        "  unclip_ratio: 1.5\n",
        encoding="utf-8",
    )
    files = {
        name: {"sha256": _sha(paddle / name), "size_bytes": (paddle / name).stat().st_size}
        for name in ("inference.json", "inference.pdiparams", "inference.yml")
    }
    manifest = stage / "stage-manifest.json"
    _write_json(
        manifest,
        {
            "schema_version": 1,
            "status": "ok",
            "profile": {
                "schema_version": 1,
                "runner": "ppocrv5-mobile-detector-paddle-export-v1",
                "training_result_sha256": "1" * 64,
                "checkpoint_sha256": "2" * 64,
                "trained_config_sha256": "3" * 64,
                "paddleocr_commit": "b03f46425e8ff4442b268ce449e3eef758146cd4",
                "corpus_id": "synthetic-v6-fixture",
                "corpus_manifest_sha256": "4" * 64,
            },
            "source_training_result": str(root / "training/result.json"),
            "paddle_directory": str(paddle),
            "files": files,
            "promotion_status": "pending_onnx_conversion_and_safety_evaluation",
        },
    )
    state = stage / "stage-state.json"
    _write_json(
        state,
        {
            "schema_version": 1,
            "status": "completed",
            "profile": json.loads(manifest.read_text())["profile"],
            "result_sha256": _sha(manifest),
        },
    )
    return {"stage": stage, "manifest": manifest, "paddle": paddle}


class DetectorCandidateConversionTest(unittest.TestCase):
    def test_preflight_binds_stage_and_constructs_pir_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate"
            result = prepare_candidate_conversion(stage_manifest=paths["manifest"], output_dir=output)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["profile"]["stage_manifest_sha256"], _sha(paths["manifest"]))
            command = result["command"]
            self.assertIn("--model_filename", command)
            self.assertIn("inference.json", command)
            self.assertIn("--params_filename", command)
            self.assertIn("inference.pdiparams", command)
            self.assertIn("--opset_version", command)
            self.assertIn("17", command)

    def test_preflight_rejects_tampered_staged_params(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            (paths["paddle"] / "inference.pdiparams").write_bytes(b"tampered")
            with self.assertRaisesRegex(CandidateConversionError, "inference.pdiparams"):
                prepare_candidate_conversion(
                    stage_manifest=paths["manifest"],
                    output_dir=Path(raw) / "candidate",
                )

    def test_conversion_emits_benchmark_manifest_and_stays_pending_safety_eval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate"

            def fake_converter(command: list[str], *, log_path: Path) -> None:
                self.assertIn("paddle2onnx", command[0])
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("converted\n", encoding="utf-8")
                output.mkdir(parents=True, exist_ok=True)
                (output / "inference.onnx").write_bytes(b"onnx-candidate")

            parity = {
                "toolchain": {
                    "python": "3.10.0",
                    "paddle": "3.2.0",
                    "paddle2onnx": "2.1.0",
                    "onnx": "1.17.0",
                    "onnxruntime": "1.23.2",
                },
                "checks": [
                    {"shape": [1, 3, 320, 320], "max_abs_error": 1e-6},
                    {"shape": [1, 3, 384, 512], "max_abs_error": 2e-6},
                ],
                "max_abs_error": 2e-6,
            }
            with (
                patch("browser_ocr.detection.candidate_convert._run_converter", side_effect=fake_converter),
                patch("browser_ocr.detection.candidate_convert._verify_onnx_and_parity", return_value=parity),
            ):
                result = run_candidate_conversion(stage_manifest=paths["manifest"], output_dir=output)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["promotion_status"], "pending_project_safety_evaluation")
            self.assertEqual(result["onnx_sha256"], _sha(output / "inference.onnx"))
            benchmark = json.loads((output / "benchmark-models.json").read_text(encoding="utf-8"))
            model = benchmark["models"][result["benchmark_model_key"]]
            self.assertEqual(model["config_model_name"], "PP-OCRv5_mobile_det")
            self.assertEqual(model["sha256"], result["onnx_sha256"])
            self.assertEqual(model["postprocess"]["box_threshold"], 0.6)

    def test_agent_control_cli_reports_conversion_preflight_as_json(self) -> None:
        from browser_ocr.detection.candidate_cli import main

        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            output = Path(raw) / "candidate"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "preflight",
                        "--stage-manifest",
                        str(paths["manifest"]),
                        "--output-dir",
                        str(output),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["profile"]["onnx_opset"], 17)

    def test_converter_dockerfile_pins_supported_cp310_toolchain(self) -> None:
        dockerfile = Path("browser_ocr/detection/Dockerfile.converter").read_text(encoding="utf-8")
        lock = Path("browser_ocr/detection/requirements-converter.lock").read_text(encoding="utf-8")
        self.assertIn("python:3.10-slim@sha256:a78e4529630cfe8c5199cafd6e0c28ee1579a13f86274396d8b6b2d80367aa3a", dockerfile)
        self.assertIn("paddlepaddle==3.2.0", lock)
        self.assertIn("paddle2onnx==2.1.0", lock)
        self.assertIn("onnx==1.17.0", lock)
        self.assertIn("onnxruntime==1.23.2", lock)


if __name__ == "__main__":
    unittest.main()