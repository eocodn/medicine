from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from browser_ocr.document_parsing.graph_evaluation_cli import build_parser, main


class GraphEvaluationCliTest(unittest.TestCase):
    def test_parser_exposes_holdout_gate_and_decoder_thresholds(self) -> None:
        args = build_parser().parse_args([
            "--model-result", "/model/result.json",
            "--dataset-manifest", "/data/val-a/manifest.json",
            "--dataset-manifest", "/data/val-b/manifest.json",
            "--output-dir", "/artifacts/parser/eval-v1",
        ])
        self.assertEqual(args.dataset_manifest, [
            "/data/val-a/manifest.json",
            "/data/val-b/manifest.json",
        ])
        self.assertFalse(args.allow_test)
        self.assertEqual(args.product_threshold, 0.75)
        self.assertEqual(args.relation_threshold, 0.72)
        self.assertEqual(args.device, "gpu")

    def test_json_mode_is_machine_readable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        expected = {"status": "ok", "document_count": 3}
        with patch(
            "browser_ocr.document_parsing.graph_evaluation_cli.run_graph_evaluation",
            return_value=expected,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--model-result", "/model/result.json",
                "--dataset-manifest", "/data/val/manifest.json",
                "--output-dir", "/artifacts/parser/eval-v1",
                "--json",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), expected)
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()