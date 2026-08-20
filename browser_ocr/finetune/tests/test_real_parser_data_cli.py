from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_ocr.document_parsing.real_data import load_real_source_manifest
from browser_ocr.finetune.real_parser_data_cli import _batch_profile, _full_document_args, build_parser, run_real_batch


class RealParserDataCliTest(unittest.TestCase):
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

    def test_defaults_match_selected_mobile_full_document_path(self) -> None:
        args = build_parser().parse_args([
            "--source-manifest", "/real/manifest.json",
            "--baseline-result", "/run/baseline.json",
            "--output-dir", "/run/real-parser",
        ])
        self.assertEqual(args.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(args.detector_edge, 640)
        self.assertEqual(args.detector_threads, 1)
        self.assertEqual(args.recognizer_device, "gpu")

    def test_each_real_photo_is_routed_through_full_document_cli_contract(self) -> None:
        args = build_parser().parse_args([
            "--source-manifest", "/real/manifest.json",
            "--baseline-result", "/run/baseline.json",
            "--output-dir", "/run/real-parser",
            "--recognizer-device", "cpu",
        ])
        full = _full_document_args(args, image_path=Path("/real/rx-1.jpg"), output_dir=Path("/run/real-parser/runtime/rx-1"))
        self.assertEqual(full.image, "/real/rx-1.jpg")
        self.assertEqual(full.output_dir, "/run/real-parser/runtime/rx-1")
        self.assertEqual(full.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(full.recognizer_device, "cpu")

    def test_completed_batch_rerun_does_not_overwrite_human_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest = self._source_manifest(root / "source")
            baseline = root / "baseline.json"
            baseline.write_text("{}", encoding="utf-8")
            output = root / "out"
            args = build_parser().parse_args([
                "--source-manifest", str(source_manifest),
                "--baseline-result", str(baseline),
                "--output-dir", str(output),
                "--recognizer-device", "cpu",
            ])
            source = load_real_source_manifest(source_manifest)
            profile = _batch_profile(args, source)
            annotations = output / "annotations"
            annotations.mkdir(parents=True)
            annotation_path = annotations / "rx-001.json"
            annotation_path.write_text('{"human_edit":"keep-me"}\n', encoding="utf-8")
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
            with patch("browser_ocr.finetune.real_parser_data_cli.run_full_document", side_effect=AssertionError("must not rerun OCR")):
                result = run_real_batch(args)
            self.assertEqual(result["status"], "ok")
            self.assertIn("keep-me", annotation_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
