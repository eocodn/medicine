from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from browser_ocr.document_parsing.graph_export_cli import build_parser, main


class GraphExportCliTest(unittest.TestCase):
    def test_parser_exposes_model_result_output_and_json_mode(self) -> None:
        args = build_parser().parse_args([
            "--model-result", "/artifacts/parser/model/result.json",
            "--output-dir", "/artifacts/parser/export",
            "--json",
        ])
        self.assertEqual(args.model_result, "/artifacts/parser/model/result.json")
        self.assertEqual(args.output_dir, "/artifacts/parser/export")
        self.assertTrue(args.json)

    def test_json_mode_is_machine_readable(self) -> None:
        output = io.StringIO()
        with patch(
            "browser_ocr.document_parsing.graph_export_cli.export_graph_model",
            return_value={"status": "ok", "model_sha256": "a" * 64},
        ) as export, redirect_stdout(output):
            code = main([
                "--model-result", "/model/result.json",
                "--output-dir", "/export",
                "--json",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ok")
        export.assert_called_once_with(model_result="/model/result.json", output_dir="/export")


if __name__ == "__main__":
    unittest.main()