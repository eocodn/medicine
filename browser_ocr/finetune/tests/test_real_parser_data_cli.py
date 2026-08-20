from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.real_parser_data_cli import _full_document_args, build_parser


class RealParserDataCliTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
