from __future__ import annotations

import unittest

from browser_ocr.document_parsing.contract import Corpus, CorpusCase, ExpectedRow, OcrBox
from browser_ocr.document_parsing.evaluation import evaluate_case, evaluate_corpus


def _row(
    row_id: str,
    product_query: str,
    draft: dict,
    evidence: dict[str, list[str]],
    uncertainty_codes: list[str] | None = None,
) -> dict:
    return {
        "row_id": row_id,
        "product_query": product_query,
        "draft": draft,
        "uncertainty_codes": uncertainty_codes or [],
        "evidence": evidence,
    }


EXPECTED = [
    _row(
        "a1",
        "약A",
        {"dose_amount": 1, "dose_unit": "tablet", "frequency_per_day": 3, "prescription_days": 5},
        {
            "product_query": ["a1"],
            "dose_amount": ["a2"],
            "dose_unit": ["a2"],
            "frequency_per_day": ["a3"],
            "prescription_days": ["a4"],
        },
    ),
    _row(
        "b1",
        "약B",
        {"dose_amount": 2, "dose_unit": "tablet", "frequency_per_day": 2, "prescription_days": 7},
        {
            "product_query": ["b1"],
            "dose_amount": ["b2"],
            "dose_unit": ["b2"],
            "frequency_per_day": ["b3"],
            "prescription_days": ["b4"],
        },
    ),
]


class EvaluationTest(unittest.TestCase):
    def test_wrong_value_from_other_medication_is_counted_as_safety_error(self) -> None:
        predicted = [
            EXPECTED[0],
            _row(
                "b1",
                "약B",
                {"dose_amount": 2, "dose_unit": "tablet", "frequency_per_day": 3, "prescription_days": 7},
                {
                    "product_query": ["b1"],
                    "dose_amount": ["b2"],
                    "dose_unit": ["b2"],
                    "frequency_per_day": ["a3"],
                    "prescription_days": ["b4"],
                },
            ),
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["matched_rows"], 2)
        self.assertEqual(result["false_exact_fields"], 1)
        self.assertEqual(result["cross_medication_associations"], 1)
        self.assertFalse(result["safety_pass"])

    def test_same_value_with_wrong_evidence_still_counts_cross_medication_association(self) -> None:
        expected = [
            _row("a1", "약A", {"frequency_per_day": 3}, {"product_query": ["a1"], "frequency_per_day": ["a3"]}),
            _row("b1", "약B", {"frequency_per_day": 3}, {"product_query": ["b1"], "frequency_per_day": ["b3"]}),
        ]
        predicted = [
            expected[0],
            _row("b1", "약B", {"frequency_per_day": 3}, {"product_query": ["b1"], "frequency_per_day": ["a3"]}),
        ]

        result = evaluate_case(expected, predicted)
        self.assertEqual(result["exact_fields"], 2)
        self.assertEqual(result["false_exact_fields"], 0)
        self.assertEqual(result["cross_medication_associations"], 1)
        self.assertFalse(result["safety_pass"])

    def test_invented_field_absent_from_expected_draft_is_false_exact(self) -> None:
        expected = [_row("a1", "약A", {"dose_amount": 1}, {"product_query": ["a1"], "dose_amount": ["a2"]})]
        predicted = [
            _row(
                "a1",
                "약A",
                {"dose_amount": 1, "frequency_per_day": 9},
                {"product_query": ["a1"], "dose_amount": ["a2"], "frequency_per_day": ["a3"]},
            )
        ]

        result = evaluate_case(expected, predicted)
        self.assertEqual(result["field_exact_accuracy"], 1.0)
        self.assertEqual(result["invented_fields"], 1)
        self.assertEqual(result["false_exact_fields"], 1)
        self.assertFalse(result["safety_pass"])

    def test_unresolved_field_is_not_false_exact(self) -> None:
        predicted = [
            EXPECTED[0],
            _row(
                "b1",
                "약B",
                {"dose_amount": 2, "dose_unit": "tablet", "frequency_per_day": None, "prescription_days": 7},
                {
                    "product_query": ["b1"],
                    "dose_amount": ["b2"],
                    "dose_unit": ["b2"],
                    "prescription_days": ["b4"],
                },
                ["AMBIGUOUS_FREQUENCY"],
            ),
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["unresolved_fields"], 1)
        self.assertEqual(result["false_exact_fields"], 0)
        self.assertEqual(result["cross_medication_associations"], 0)
        self.assertTrue(result["safety_pass"])

    def test_repeated_product_queries_align_by_evidence_backed_row_id(self) -> None:
        expected = [
            _row("a1", "약A", {"dose_amount": 1}, {"product_query": ["a1"], "dose_amount": ["a2"]}),
            _row("b1", "약A", {"dose_amount": 2}, {"product_query": ["b1"], "dose_amount": ["b2"]}),
        ]
        predicted = [expected[1], expected[0]]

        result = evaluate_case(expected, predicted)
        self.assertEqual(result["matched_rows"], 2)
        self.assertEqual(result["missing_rows"], 0)
        self.assertEqual(result["unexpected_rows"], 0)
        self.assertEqual(result["exact_fields"], 2)

    def test_missing_and_unexpected_rows_are_reported_separately(self) -> None:
        predicted = [
            EXPECTED[0],
            _row("c1", "약C", {}, {"product_query": ["c1"]}),
        ]
        result = evaluate_case(EXPECTED, predicted)
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["unexpected_rows"], 1)
        self.assertEqual(result["matched_rows"], 1)

    def test_empty_prediction_rows_are_valid_fail_closed_output(self) -> None:
        expected_row = ExpectedRow(
            row_id="b1",
            product_query="약A",
            draft={"frequency_per_day": 3},
            uncertainty_codes=(),
            evidence={"product_query": ("b1",), "frequency_per_day": ("b2",)},
        )
        corpus = Corpus(
            schema_version=2,
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
                        OcrBox(
                            box_id="b2",
                            text="복용횟수: 3회",
                            confidence=1.0,
                            polygon=((0, 30), (100, 30), (100, 50), (0, 50)),
                        ),
                    ),
                    expected_rows=(expected_row,),
                ),
            ),
        )
        result = evaluate_corpus(
            corpus,
            {"schema_version": 2, "predictions": [{"case_id": "simple", "rows": []}]},
        )
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["false_exact_fields"], 0)
        self.assertTrue(result["safety_pass"])


if __name__ == "__main__":
    unittest.main()