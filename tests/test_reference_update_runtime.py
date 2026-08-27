from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

from medicine_reference.reference_update import REFERENCE_CONTRACT_MAJOR, verify_reference_database
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mobile import REFERENCE_CONTRACT_MAJOR as BUILDER_CONTRACT_MAJOR, build_mobile_database
from tests.canonical_fixture_support import make_canonical_db


class ReferenceUpdateRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical.sqlite"
        self.mobile = self.root / "mobile.sqlite"
        self.manifest = self.root / "mobile.manifest.json"
        make_canonical_db(self.canonical)
        self.release = build_mobile_database(self.canonical, self.mobile, manifest_path=self.manifest)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_runtime_verifier_accepts_exact_mobile_release_identity(self) -> None:
        result = verify_reference_database(
            self.mobile,
            expected_contract_major=self.release["contract_major"],
            expected_dataset_id=self.release["dataset_id"],
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["dataset_id"], self.release["dataset_id"])
        self.assertEqual(result["contract_major"], 1)

    def test_runtime_verifier_rejects_dataset_identity_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "dataset identity"):
            verify_reference_database(
                self.mobile,
                expected_contract_major=1,
                expected_dataset_id="sha256:" + "0" * 64,
            )

    def test_runtime_verifier_does_not_bind_acceptance_to_source_snapshot_metadata(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            con.execute(
                "UPDATE source_snapshots SET sha256=? "
                "WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'",
                ("f" * 64,),
            )
            con.commit()
        result = verify_reference_database(
            self.mobile,
            expected_contract_major=1,
            expected_dataset_id=self.release["dataset_id"],
        )
        self.assertEqual(result["status"], "verified")

    def test_runtime_verifier_requires_materialized_contract_semantics_table(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            con.execute("DROP TABLE reference_criterion_semantics")
            con.commit()

        with self.assertRaisesRegex(ValueError, "reference contract schema"):
            verify_reference_database(
                self.mobile,
                expected_contract_major=1,
                expected_dataset_id=self.release["dataset_id"],
            )

    def test_runtime_verifier_requires_semantic_expectation_table(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            con.execute("DROP TABLE reference_semantic_expectations")
            con.commit()

        with self.assertRaisesRegex(ValueError, "reference contract schema"):
            verify_reference_database(
                self.mobile,
                expected_contract_major=1,
                expected_dataset_id=self.release["dataset_id"],
            )

    def test_runtime_verifier_rejects_missing_runtime_required_product_column(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            con.execute("ALTER TABLE products DROP COLUMN manufacturer")
            con.commit()

        with self.assertRaisesRegex(ValueError, "products.*manufacturer"):
            verify_reference_database(
                self.mobile,
                expected_contract_major=1,
                expected_dataset_id=self.release["dataset_id"],
            )

    def test_runtime_verifier_ignores_non_runtime_source_snapshot_provenance_columns(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            con.execute("ALTER TABLE source_snapshots DROP COLUMN source_locator")
            con.commit()

        result = verify_reference_database(
            self.mobile,
            expected_contract_major=1,
            expected_dataset_id=self.release["dataset_id"],
        )
        self.assertEqual(result["status"], "verified")

    def test_runtime_verifier_rejects_malformed_known_semantic_payload(self) -> None:
        with closing(sqlite3.connect(self.mobile)) as con, con:
            criterion_rule_id = con.execute(
                "SELECT id FROM ingredient_rules ORDER BY id LIMIT 1"
            ).fetchone()[0]
            ordinal = con.execute(
                """SELECT COALESCE(MAX(ordinal),-1)+1
                   FROM reference_criterion_semantics WHERE criterion_rule_id=?""",
                (criterion_rule_id,),
            ).fetchone()[0]
            con.execute(
                """INSERT INTO reference_criterion_semantics(
                       criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
                       fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
                   ) VALUES(?,?,'applicability_condition','runtime_evaluable','minimum_separation',
                            'review_required','timing','24시간 이내 병용금기','{}','24시간 이내 병용금기')""",
                (criterion_rule_id, ordinal),
            )
            con.commit()

        with self.assertRaisesRegex(ValueError, "minimum_separation.*payload"):
            verify_reference_database(
                self.mobile,
                expected_contract_major=1,
                expected_dataset_id=self.release["dataset_id"],
            )

    def test_runtime_contract_major_is_shared_with_mobile_builder(self) -> None:
        self.assertEqual(REFERENCE_CONTRACT_MAJOR, BUILDER_CONTRACT_MAJOR)

    def test_headless_cli_verifies_mobile_runtime_database(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = canonical_main([
                "mobile-verify-runtime",
                "--db", str(self.mobile),
                "--contract-major", "1",
                "--dataset-id", self.release["dataset_id"],
                "--json",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["dataset_id"], self.release["dataset_id"])
        self.assertEqual(payload["contract_major"], 1)


if __name__ == "__main__":
    unittest.main()
