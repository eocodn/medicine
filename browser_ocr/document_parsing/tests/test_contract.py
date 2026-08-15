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
                "text": "약명: 약A",
                "confidence": 1.0,
                "polygon": [[10, 10], [80, 10], [80, 30], [10, 30]],
            },
            {
                "box_id": "b2",
                "text": "복용법: 1회 1정 1일 3회 5일",
                "confidence": 1.0,
                "polygon": [[10, 40], [180, 40], [180, 60], [10, 60]],
            }
        ],
        "expected_rows": [
            {
                "row_id": "b1",
                "product_query": "약A",
                "draft": {
                    "dose_amount": 1,
                    "dose_unit": "tablet",
                    "frequency_per_day": 3,
                    "prescription_days": 5,
                },
                "uncertainty_codes": [],
                "evidence": {
                    "product_query": ["b1"],
                    "dose_amount": ["b2"],
                    "dose_unit": ["b2"],
                    "frequency_per_day": ["b2"],
                    "prescription_days": ["b2"],
                },
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
        corpus = self._load({"schema_version": 2, "cases": [_case()]})
        self.assertEqual(corpus.schema_version, 2)
        self.assertEqual(len(corpus.cases), 1)
        self.assertEqual(corpus.cases[0].boxes[0].text, "약명: 약A")
        self.assertEqual(corpus.cases[0].expected_rows[0].product_query, "약A")
        self.assertEqual(corpus.cases[0].expected_rows[0].evidence["product_query"], ("b1",))

    def test_rejects_invalid_confidence_and_geometry(self) -> None:
        case = _case()
        case["boxes"][0]["confidence"] = 1.1
        with self.assertRaisesRegex(CorpusError, "confidence"):
            self._load({"schema_version": 2, "cases": [case]})

        case = _case()
        case["boxes"][0]["polygon"] = [[0, 0], [1, 0], [1, 1]]
        with self.assertRaisesRegex(CorpusError, "polygon"):
            self._load({"schema_version": 2, "cases": [case]})

    def test_rejects_duplicate_case_and_box_ids(self) -> None:
        case = _case()
        duplicate = _case()
        with self.assertRaisesRegex(CorpusError, "case_id"):
            self._load({"schema_version": 2, "cases": [case, duplicate]})

        case = _case()
        case["boxes"].append(dict(case["boxes"][0]))
        with self.assertRaisesRegex(CorpusError, "box_id"):
            self._load({"schema_version": 2, "cases": [case]})

    def test_rejects_non_synthetic_sources_and_unknown_fields(self) -> None:
        case = _case()
        case["source_kind"] = "real_patient_photo"
        with self.assertRaisesRegex(CorpusError, "source_kind"):
            self._load({"schema_version": 2, "cases": [case]})

        case = _case()
        case["raw_ocr_text"] = "must not be accepted"
        with self.assertRaisesRegex(CorpusError, "unsupported"):
            self._load({"schema_version": 2, "cases": [case]})

    def test_repeated_product_queries_are_allowed_when_row_evidence_is_distinct(self) -> None:
        case = _case()
        case["boxes"].extend(
            [
                {
                    "box_id": "b3",
                    "text": "약명: 약A",
                    "confidence": 1.0,
                    "polygon": [[10, 80], [80, 80], [80, 100], [10, 100]],
                },
                {
                    "box_id": "b4",
                    "text": "복용법: 1회 2정 1일 2회 7일",
                    "confidence": 1.0,
                    "polygon": [[10, 110], [180, 110], [180, 130], [10, 130]],
                },
            ]
        )
        case["expected_rows"].append(
            {
                "row_id": "b3",
                "product_query": "약A",
                "draft": {"dose_amount": 2, "dose_unit": "tablet", "frequency_per_day": 2, "prescription_days": 7},
                "uncertainty_codes": [],
                "evidence": {
                    "product_query": ["b3"],
                    "dose_amount": ["b4"],
                    "dose_unit": ["b4"],
                    "frequency_per_day": ["b4"],
                    "prescription_days": ["b4"],
                },
            }
        )

        corpus = self._load({"schema_version": 2, "cases": [case]})
        self.assertEqual([row.product_query for row in corpus.cases[0].expected_rows], ["약A", "약A"])

    def test_rejects_evidence_outside_case_and_unbacked_row_id(self) -> None:
        case = _case()
        case["expected_rows"][0]["evidence"]["dose_amount"] = ["missing"]
        with self.assertRaisesRegex(CorpusError, "evidence"):
            self._load({"schema_version": 2, "cases": [case]})

        case = _case()
        case["expected_rows"][0]["row_id"] = "other"
        with self.assertRaisesRegex(CorpusError, "row_id"):
            self._load({"schema_version": 2, "cases": [case]})


if __name__ == "__main__":
    unittest.main()