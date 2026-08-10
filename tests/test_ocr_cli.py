from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medicine_app.cli import main


class OcrInspectCliTest(unittest.TestCase):
    def test_ocr_inspect_json_reads_stdin_without_database_or_writes(self) -> None:
        payload = {
            "version": 1,
            "operation_id": "cli-1",
            "hints": {"product_ref": "MFDS-A", "dose": "1정", "frequency": "하루 2회", "days": "5일", "times": ["08:00", "20:00"]},
        }
        with patch("sys.stdin.read", return_value=json.dumps(payload)), patch("builtins.print") as output:
            self.assertEqual(main(["ocr-inspect", "--input", "-", "--json"]), 0)
        rendered = json.loads(output.call_args.args[0])
        self.assertEqual(rendered["draft"]["frequency_per_day"], 2)

    def test_ocr_inspect_reports_malformed_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hints.json"
            path.write_text('{"version": 1, "operation_id": "bad", "hints": {"raw_text": "x"}}')
            with patch("builtins.print") as output:
                self.assertNotEqual(main(["ocr-inspect", "--input", str(path), "--json"]), 0)
            rendered = json.loads(output.call_args.args[0])
            self.assertTrue(rendered["error"])
            self.assertFalse((Path(temporary) / "personal.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
