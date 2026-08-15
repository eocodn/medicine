from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.contract import CorpusError, load_corpus


def _case() -> dict:
    return {
        "case_id": "two-drug-table",
        "source_kind": "synthetic",
        "scenario_tags": ["prescription_table", "multi_medication"],
        "risk_tags": ["row_association"],
        "boxes": [
            {
                "box_id": "b1",
                "text": "약품명",
                "confidence": 1.0,
                "polygon": [[10, 10], [80, 10], [80, 30], [10, 30]],
            }
        ],
        "expected_rows": [
            {
                "row_id": "m1",
                "product_query": "약A",
                "draft": {
                    "dose_amount": 1,
                    "dose_unit": "tablet",
                    "frequency_per_day": 3,
                    "prescription_days": 5,
                },
                "uncertainty_codes": [],
            }
        ],
    }


class CorpusContractTest(unittest.TestCase):
    def _load(self, value: dict):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return load_corpus(path)

    def test_loads_strict_versioned_synthetic_box_corpus(self) -> None:
        corpus = self._load({"schema_version": 1, "cases": [_case()]})
        self.assertEqual(corpus.schema_version, 1)
        self.assertEqual(len(corpus.cases), 1)
        self.assertEqual(corpus.cases[0].boxes[0].text, "약품명")
        self.assertEqual(corpus.cases[0].expected_rows[0].product_query, "약A")

    def test_rejects_invalid_confidence_and_geometry(self) -> None:
        case = _case()
        case["boxes"][0]["confidence"] = 1.1
        with self.assertRaisesRegex(CorpusError, "confidence"):
            self._load({"schema_version": 1, "cases": [case]})

        case = _case()
        case["boxes"][0]["polygon"] = [[0, 0], [1, 0], [1, 1]]
        with self.assertRaisesRegex(CorpusError, "polygon"):
            self._load({"schema_version": 1, "cases": [case]})

    def test_rejects_duplicate_case_and_box_ids(self) -> None:
        case = _case()
        duplicate = _case()
        with self.assertRaisesRegex(CorpusError, "case_id"):
            self._load({"schema_version": 1, "cases": [case, duplicate]})

        case = _case()
        case["boxes"].append(dict(case["boxes"][0]))
        with self.assertRaisesRegex(CorpusError, "box_id"):
            self._load({"schema_version": 1, "cases": [case]})

    def test_rejects_non_synthetic_sources_and_unknown_fields(self) -> None:
        case = _case()
        case["source_kind"] = "real_patient_photo"
        with self.assertRaisesRegex(CorpusError, "source_kind"):
            self._load({"schema_version": 1, "cases": [case]})

        case = _case()
        case["raw_ocr_text"] = "must not be accepted"
        with self.assertRaisesRegex(CorpusError, "unsupported"):
            self._load({"schema_version": 1, "cases": [case]})


if __name__ == "__main__":
    unittest.main()