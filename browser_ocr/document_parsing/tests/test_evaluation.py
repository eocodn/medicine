from __future__ import annotations

import unittest

from browser_ocr.document_parsing.contract import Corpus, CorpusCase, ExpectedRow, OcrBox
from browser_ocr.document_parsing.evaluation import evaluate_case, evaluate_corpus


EXPECTED = [
    {
        "row_id": "a",
        "product_query": "약A",
        "draft": {
            "dose_amount": 1,
            "dose_unit": "tablet",
            "frequency_per_day": 3,
            "prescription_days": 5,
        },
        "uncertainty_codes": [],
    },
    {
        "row_id": "b",
        "product_query": "약B",
        "draft": {
            "dose_amount": 2,
            "dose_unit": "tablet",
            "frequency_per_day": 2,
            "prescription_days": 7,
        },
        "uncertainty_codes": [],
    },
]


class EvaluationTest(unittest.TestCase):
    def test_cross_medication_exact_value_is_counted_as_safety_error(self) -> None:
        predicted = [
            EXPECTED[0],
            {
                "row_id": "generated-b",
                "product_query": "약B",
                "draft": {
                    "dose_amount": 2,
                    "dose_unit": "tablet",
                    "frequency_per_day": 3,
                    "prescription_days": 7,
                },
                "uncertainty_codes": [],
            },
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["matched_rows"], 2)
        self.assertEqual(result["false_exact_fields"], 1)
        self.assertEqual(result["cross_medication_associations"], 1)
        self.assertFalse(result["safety_pass"])

    def test_unresolved_field_is_not_false_exact(self) -> None:
        predicted = [
            EXPECTED[0],
            {
                "row_id": "generated-b",
                "product_query": "약B",
                "draft": {
                    "dose_amount": 2,
                    "dose_unit": "tablet",
                    "frequency_per_day": None,
                    "prescription_days": 7,
                },
                "uncertainty_codes": ["AMBIGUOUS_FREQUENCY"],
            },
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["unresolved_fields"], 1)
        self.assertEqual(result["false_exact_fields"], 0)
        self.assertEqual(result["cross_medication_associations"], 0)
        self.assertTrue(result["safety_pass"])

    def test_missing_and_unexpected_rows_are_reported_separately(self) -> None:
        predicted = [
            EXPECTED[0],
            {
                "row_id": "c",
                "product_query": "약C",
                "draft": {},
                "uncertainty_codes": [],
            },
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["unexpected_rows"], 1)
        self.assertEqual(result["matched_rows"], 1)


    def test_empty_prediction_rows_are_valid_fail_closed_output(self) -> None:
        expected_row = ExpectedRow(
            row_id="a",
            product_query="약A",
            draft={"frequency_per_day": 3},
            uncertainty_codes=(),
        )
        corpus = Corpus(
            schema_version=1,
            cases=(
                CorpusCase(
                    case_id="simple",
                    source_kind="synthetic",
                    scenario_tags=("medication_bag",),
                    risk_tags=("row_association",),
                    boxes=(
                        OcrBox(
                            box_id="b1",
                            text="약명: 약A",
                            confidence=1.0,
                            polygon=((0, 0), (100, 0), (100, 20), (0, 20)),
                        ),
                    ),
                    expected_rows=(expected_row,),
                ),
            ),
        )
        result = evaluate_corpus(
            corpus,
            {"schema_version": 1, "predictions": [{"case_id": "simple", "rows": []}]},
        )
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["false_exact_fields"], 0)
        self.assertTrue(result["safety_pass"])


if __name__ == "__main__":
    unittest.main()