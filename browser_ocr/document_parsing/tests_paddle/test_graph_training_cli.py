from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from browser_ocr.document_parsing.graph_training_cli import build_parser, main


class GraphTrainingCliTest(unittest.TestCase):
    def test_parser_exposes_repeatable_dataset_inputs_and_mobile_defaults(self) -> None:
        args = build_parser().parse_args([
            "--train-manifest", "/data/train-a/manifest.json",
            "--train-manifest", "/data/train-b/manifest.json",
            "--train-weight", "0.7",
            "--train-weight", "0.3",
            "--val-manifest", "/data/val/manifest.json",
            "--run-dir", "/artifacts/parser/model-v1",
        ])
        self.assertEqual(args.train_manifest, [
            "/data/train-a/manifest.json",
            "/data/train-b/manifest.json",
        ])
        self.assertEqual(args.train_weight, [0.7, 0.3])
        self.assertEqual(args.val_manifest, ["/data/val/manifest.json"])
        self.assertEqual(args.hidden_dim, 96)
        self.assertEqual(args.layers, 2)
        self.assertEqual(args.neighbor_count, 12)
        self.assertEqual(args.pair_hidden_dim, 64)
        self.assertEqual(args.device, "gpu")

    def test_json_mode_returns_machine_readable_result_and_error(self) -> None:
        result = {"status": "ok", "best_epoch": 2}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "browser_ocr.document_parsing.graph_training_cli.run_graph_training",
            return_value=result,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--train-manifest", "/data/train/manifest.json",
                "--val-manifest", "/data/val/manifest.json",
                "--run-dir", "/artifacts/parser/model-v1",
                "--json",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), result)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "browser_ocr.document_parsing.graph_training_cli.run_graph_training",
            side_effect=ValueError("bad profile"),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "--train-manifest", "/data/train/manifest.json",
                "--val-manifest", "/data/val/manifest.json",
                "--run-dir", "/artifacts/parser/model-v1",
                "--json",
            ])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"status": "error", "error": "bad profile"})


if __name__ == "__main__":
    unittest.main()