from __future__ import annotations

import sqlite3
import unittest

from medicine_app.interaction_timing import interaction_timing_applies
from medicine_app.reference_semantics import (
    _mfds_criterion_note_requires_review,
    _remark_interaction_timing,
)


class ReferenceSemanticsRuntimeTest(unittest.TestCase):
    @staticmethod
    def _connection(evaluator: str, payload_json: str, source_remark: str) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.execute(
            """CREATE TABLE reference_semantic_expectations(
                   criterion_rule_id INTEGER PRIMARY KEY,
                   expected_fact_count INTEGER NOT NULL
               )"""
        )
        con.execute(
            """CREATE TABLE reference_criterion_semantics(
                   criterion_rule_id INTEGER NOT NULL,
                   ordinal INTEGER NOT NULL,
                   semantic_role TEXT NOT NULL,
                   evaluation_mode TEXT NOT NULL,
                   evaluator_kind TEXT NOT NULL,
                   fallback_action TEXT NOT NULL,
                   qualifier_type TEXT NOT NULL,
                   display_text TEXT NOT NULL,
                   structured_payload_json TEXT NOT NULL,
                   source_remark TEXT NOT NULL
               )"""
        )
        con.execute("INSERT INTO reference_semantic_expectations VALUES(1,1)")
        con.execute(
            """INSERT INTO reference_criterion_semantics VALUES(
                   1,0,'applicability_condition','runtime_evaluable',?,
                   'review_required','timing',?,?,?
               )""",
            (evaluator, source_remark, payload_json, source_remark),
        )
        return con

    @staticmethod
    def _missing_semantics_connection() -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.execute(
            """CREATE TABLE reference_semantic_expectations(
                   criterion_rule_id INTEGER PRIMARY KEY,
                   expected_fact_count INTEGER NOT NULL
               )"""
        )
        con.execute(
            """CREATE TABLE reference_criterion_semantics(
                   criterion_rule_id INTEGER NOT NULL,
                   ordinal INTEGER NOT NULL,
                   semantic_role TEXT NOT NULL,
                   evaluation_mode TEXT NOT NULL,
                   evaluator_kind TEXT NOT NULL,
                   fallback_action TEXT NOT NULL,
                   qualifier_type TEXT NOT NULL,
                   display_text TEXT NOT NULL,
                   structured_payload_json TEXT NOT NULL,
                   source_remark TEXT NOT NULL
               )"""
        )
        con.execute("INSERT INTO reference_semantic_expectations VALUES(1,1)")
        return con

    @staticmethod
    def _timing(con: sqlite3.Connection) -> dict:
        return _remark_interaction_timing(
            {
                "criterion_rule_id": 1,
                "criterion_ingredient_name": "A",
                "criterion_paired_ingredient_name": "B",
            },
            "병용 시에만 적용",
            con,
        )

    def test_unknown_runtime_evaluator_with_review_fallback_cannot_suppress_finding(self) -> None:
        with self._connection("future_timing_rule", "{}", "미래 조건") as con:
            timing = self._timing(con)

        self.assertEqual(timing["status"], "not_evaluable")
        self.assertTrue(
            interaction_timing_applies(
                timing,
                {"start_date": "2026-08-20", "end_date": "2026-08-20"},
                {"start_date": "2026-08-01", "end_date": "2026-08-01"},
                candidate_side="left",
            )
        )

    def test_malformed_known_runtime_evaluator_fails_conservatively(self) -> None:
        with self._connection("minimum_separation", "{}", "24시간 이내 병용금기") as con:
            timing = self._timing(con)

        self.assertEqual(timing["status"], "not_evaluable")
        self.assertTrue(
            interaction_timing_applies(
                timing,
                {"start_date": "2026-08-20", "end_date": "2026-08-20"},
                {"start_date": "2026-08-18", "end_date": "2026-08-18"},
                candidate_side="left",
            )
        )

    def test_missing_expected_semantics_fails_conservatively(self) -> None:
        with self._missing_semantics_connection() as con:
            timing = _remark_interaction_timing(
                {
                    "criterion_rule_id": 1,
                    "criterion_qualifier_note": "세부 적용 조건",
                    "criterion_ingredient_name": "A",
                    "criterion_paired_ingredient_name": "B",
                },
                "병용 시에만 적용",
                con,
            )

        self.assertEqual(timing["status"], "not_evaluable")
        self.assertEqual(timing["kind"], "missing_contract_semantics")

    def test_intentional_zero_fact_semantics_do_not_require_runtime_review(self) -> None:
        with self._missing_semantics_connection() as con:
            con.execute("DELETE FROM reference_semantic_expectations")
            requires_review = _mfds_criterion_note_requires_review(
                {
                    "criterion_rule_id": 1,
                    "criterion_qualifier_note": "단일제·복합제 포함",
                    "match_method": "mfds_ingredient_code",
                },
                con,
            )

        self.assertFalse(requires_review)


if __name__ == "__main__":
    unittest.main()