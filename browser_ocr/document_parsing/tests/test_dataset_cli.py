from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from browser_ocr.document_parsing.dataset_cli import build_parser, main
from browser_ocr.document_parsing.training_dataset import write_parser_dataset


class ParserDatasetCliTest(unittest.TestCase):
    def test_parser_exposes_dataset_controls(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["validate", "--manifest", "/tmp/manifest.json", "--json"])
        self.assertEqual(args.command, "validate")
        args = parser.parse_args([
            "build-synthetic", "--truth-samples", "/tmp/truth.jsonl", "--output-dir", "/tmp/out",
            "--dataset-id", "fixture", "--observation-kind", "synthetic_ocr", "--split", "train",
        ])
        self.assertEqual(args.observation_kind, "synthetic_ocr")

    def test_validate_reports_machine_readable_summary(self) -> None:
        document = {
            "document_id": "doc",
            "split": "val",
            "source_kind": "synthetic",
            "image_sha256": "a" * 64,
            "width": 100,
            "height": 100,
            "layout_family": "fixture",
            "scenario_tags": [],
            "risk_tags": [],
            "privacy": {"contains_patient_data": False, "deidentified": False},
            "observation": {"kind": "oracle", "profile": {}, "nodes": []},
            "relations": [],
            "gold_rows": [],
            "annotation_status": "complete",
        }
        with tempfile.TemporaryDirectory() as raw:
            manifest = write_parser_dataset(Path(raw), dataset_id="fixture", documents=[document])
            output = StringIO()
            with redirect_stdout(output):
                code = main(["validate", "--manifest", str(manifest), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["documents"], 1)
            self.assertEqual(payload["splits"]["val"], 1)


if __name__ == "__main__":
    unittest.main()
