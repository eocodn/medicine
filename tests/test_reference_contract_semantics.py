from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mobile import build_mobile_database
from medicine_canonical.reference_contracts.v1 import (
    REFERENCE_CONTRACT_MAJOR,
    export_reference_database,
    semantic_facts_for_reviewed_remark,
    verify_reference_database,
)
from medicine_canonical.reference_contracts.registry import (
    build_supported_contract_window,
    supported_contract_majors,
)
from medicine_reference.mfds_remark_registry import reviewed_mfds_remark
from tests.canonical_fixture_support import add_linked_rule, add_product
from tests.test_safety_coverage import make_canonical_db


class ReferenceContractSemanticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical = root / "canonical.sqlite"
        self.mobile = root / "mobile.sqlite"
        make_canonical_db(self.canonical)
        with sqlite3.connect(self.canonical) as con:
            add_product(con, "SEM-A", "semantic A", "SemanticA", dosage_form="정제")
            add_product(con, "SEM-B", "semantic B", "SemanticB", dosage_form="정제")
            add_product(con, "SEM-M", "semantic minoxidil", "Minoxidil", dosage_form="정제")
            add_linked_rule(
                con,
                category="combination_contraindication",
                item_seq="SEM-A",
                ingredient="SemanticA",
                paired_item_seq="SEM-B",
                paired_ingredient="SemanticB",
                criterion_qualifier_note="24시간 이내 병용금기",
                details="병용금기",
            )
            add_linked_rule(
                con,
                category="therapeutic_duplication_caution",
                item_seq="SEM-M",
                ingredient="Minoxidil",
                effect_name="혈압강하작용의약품",
                criterion_qualifier_note="외용제는 제외",
            )
            con.commit()
        self.release = build_mobile_database(self.canonical, self.mobile)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _semantic_for_remark(self, remark: str) -> sqlite3.Row:
        with sqlite3.connect(self.mobile) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                """SELECT * FROM reference_criterion_semantics
                   WHERE source_remark=? ORDER BY criterion_rule_id,ordinal LIMIT 1""",
                (remark,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row

    def test_contract_meta_separates_public_contract_from_build_diagnostics(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            contract = dict(con.execute("SELECT key,value FROM reference_contract_meta"))
            build = dict(con.execute("SELECT key,value FROM reference_build_meta"))
        self.assertEqual(REFERENCE_CONTRACT_MAJOR, 1)
        self.assertEqual(contract["contract_major"], "1")
        self.assertEqual(contract["dataset_id"], self.release["dataset_id"])
        self.assertEqual(build["canonical_schema_version"], "10")
        self.assertEqual(build["physical_policy_version"], "8")
        self.assertNotIn("canonical_schema_version", contract)
        self.assertNotIn("physical_policy_version", contract)

    def test_checked_in_v1_exporter_and_verifier_are_frozen_entry_points(self) -> None:
        exported_db = self.mobile.with_name("v1-export.sqlite")
        exported_manifest = self.mobile.with_name("v1-export.manifest.json")

        with mock.patch(
            "medicine_canonical.mobile.build_mobile_database",
            side_effect=AssertionError("mutable default exporter must not own frozen contract v1"),
        ):
            release = export_reference_database(
                self.canonical,
                exported_db,
                manifest_path=exported_manifest,
            )
        with mock.patch(
            "medicine_app.reference_update.verify_reference_database",
            side_effect=AssertionError("mutable verifier dispatcher must not own frozen contract v1"),
        ):
            verified = verify_reference_database(
                exported_db,
                REFERENCE_CONTRACT_MAJOR,
                release["dataset_id"],
            )

        self.assertEqual(release["contract_major"], 1)
        self.assertEqual(verified["status"], "verified")

    def test_supported_contract_window_builder_emits_every_registered_major(self) -> None:
        output = self.mobile.parent / "contract-window"

        result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(supported_contract_majors(), (1,))
        self.assertEqual(result["current_contract_major"], 1)
        self.assertEqual(result["minimum_supported_contract_major"], 1)
        self.assertEqual([entry["contract_major"] for entry in result["contracts"]], [1])
        self.assertTrue(Path(result["contracts"][0]["database"]).is_file())
        self.assertTrue(Path(result["contracts"][0]["manifest"]).is_file())

    def test_reference_window_build_cli_uses_registered_contract_set(self) -> None:
        output = self.mobile.parent / "cli-contract-window"
        stdout = StringIO()

        with redirect_stdout(stdout):
            code = canonical_main([
                "reference-window-build",
                "--db", str(self.canonical),
                "--output-dir", str(output),
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["current_contract_major"], 1)
        self.assertEqual([entry["contract_major"] for entry in payload["contracts"]], [1])

    def test_review_required_remark_is_materialized_as_opaque_condition(self) -> None:
        row = self._semantic_for_remark(
            "다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여"
        )
        self.assertEqual(row["semantic_role"], "applicability_condition")
        self.assertEqual(row["evaluation_mode"], "review_required")
        self.assertEqual(row["evaluator_kind"], "opaque_condition")
        self.assertEqual(row["fallback_action"], "review_required")
        self.assertEqual(json.loads(row["structured_payload_json"]), {})

    def test_interaction_window_is_materialized_as_runtime_evaluator(self) -> None:
        row = self._semantic_for_remark("24시간 이내 병용금기")
        self.assertEqual(row["evaluation_mode"], "runtime_evaluable")
        self.assertEqual(row["evaluator_kind"], "minimum_separation")
        self.assertEqual(row["fallback_action"], "review_required")
        self.assertEqual(
            json.loads(row["structured_payload_json"]),
            {"direction": "symmetric", "hours": 24},
        )

    def test_form_exclusion_is_materialized_as_runtime_evaluator(self) -> None:
        row = self._semantic_for_remark("외용제는 제외")
        self.assertEqual(row["evaluator_kind"], "excluded_route")
        self.assertEqual(json.loads(row["structured_payload_json"]), {"route": "topical"})

    def test_build_scope_review_produces_no_runtime_fact(self) -> None:
        reviewed = reviewed_mfds_remark("dose_caution", "단일제·복합제 포함")
        self.assertIsNotNone(reviewed)
        assert reviewed is not None
        self.assertEqual(semantic_facts_for_reviewed_remark(reviewed), ())


if __name__ == "__main__":
    unittest.main()