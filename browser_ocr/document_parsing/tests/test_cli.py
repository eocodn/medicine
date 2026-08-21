from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTest(unittest.TestCase):
    def test_validate_corpus_json(self) -> None:
        corpus = {
            "schema_version": 2,
            "cases": [
                {
                    "case_id": "simple",
                    "source_kind": "synthetic",
                    "scenario_tags": ["medication_bag"],
                    "risk_tags": ["row_association"],
                    "boxes": [
                        {
                            "box_id": "b1",
                            "text": "약명: 약A",
                            "confidence": 1.0,
                            "polygon": [[0, 0], [100, 0], [100, 20], [0, 20]],
                        }
                    ],
                    "expected_rows": [
                        {
                            "row_id": "b1",
                            "product_query": "약A",
                            "draft": {},
                            "uncertainty_codes": [],
                            "evidence": {"product_query": ["b1"]},
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "browser_ocr.document_parsing.cli",
                    "validate-corpus",
                    "--corpus",
                    str(path),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["case_count"], 1)


    def test_evaluate_json_accepts_fail_closed_empty_rows(self) -> None:
        corpus = {
            "schema_version": 2,
            "cases": [
                {
                    "case_id": "simple",
                    "source_kind": "synthetic",
                    "scenario_tags": ["medication_bag"],
                    "risk_tags": ["row_association"],
                    "boxes": [
                        {
                            "box_id": "b1",
                            "text": "약명: 약A",
                            "confidence": 1.0,
                            "polygon": [[0, 0], [100, 0], [100, 20], [0, 20]],
                        }
                    ],
                    "expected_rows": [
                        {
                            "row_id": "b1",
                            "product_query": "약A",
                            "draft": {"frequency_per_day": 3},
                            "uncertainty_codes": [],
                            "evidence": {"product_query": ["b1"], "frequency_per_day": ["b1"]},
                        }
                    ],
                }
            ],
        }
        predictions = {
            "schema_version": 2,
            "predictions": [{"case_id": "simple", "rows": []}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            corpus_path = Path(tmp) / "corpus.json"
            predictions_path = Path(tmp) / "predictions.json"
            corpus_path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
            predictions_path.write_text(json.dumps(predictions, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "browser_ocr.document_parsing.cli",
                    "evaluate",
                    "--corpus",
                    str(corpus_path),
                    "--predictions",
                    str(predictions_path),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["missing_rows"], 1)
        self.assertTrue(result["safety_pass"])



if __name__ == "__main__":
    unittest.main()