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
    logical_dataset_id,
    logical_dataset_id_oracle,
    semantic_facts_for_reviewed_remark,
    verify_reference_database,
)
from medicine_canonical.reference_contracts.registry import (
    ReferenceContractImplementation,
    build_supported_contract_window,
    supported_contract_majors,
)
from medicine_reference.mfds_remark_registry import reviewed_mfds_remark
from tests.canonical_fixture_support import add_linked_rule, add_product
from tests.canonical_fixture_support import make_canonical_db


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

    def test_server_verifier_rejects_missing_reviewed_semantic_materialization(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            criterion_rule_id = con.execute(
                """SELECT criterion_rule_id FROM reference_criterion_semantics
                   WHERE source_remark=? ORDER BY criterion_rule_id LIMIT 1""",
                ("다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여",),
            ).fetchone()[0]
            con.execute(
                "DELETE FROM reference_criterion_semantics WHERE criterion_rule_id=?",
                (criterion_rule_id,),
            )
            con.execute(
                "DELETE FROM reference_semantic_expectations WHERE criterion_rule_id=?",
                (criterion_rule_id,),
            )
            dataset_id = logical_dataset_id(con)
            con.execute(
                "UPDATE reference_contract_meta SET value=? WHERE key='dataset_id'",
                (dataset_id,),
            )
            con.commit()

        with self.assertRaisesRegex(ValueError, "semantic materialization"):
            verify_reference_database(self.mobile, REFERENCE_CONTRACT_MAJOR, dataset_id)

    def test_server_verifier_recomputes_frozen_logical_dataset_identity(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            con.execute(
                "UPDATE products SET product_name=product_name || ' mutated' WHERE item_seq='SEM-A'"
            )
            con.commit()

        with self.assertRaisesRegex(ValueError, "logical dataset identity"):
            verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                self.release["dataset_id"],
            )

    def test_strict_server_verifier_uses_frozen_oracle_not_fast_executor(self) -> None:
        with (
            mock.patch(
                "medicine_canonical.reference_contracts.v1.logical_dataset_id_oracle",
                return_value=self.release["dataset_id"],
            ) as oracle,
            mock.patch(
                "medicine_canonical.reference_contracts.v1.logical_dataset_id",
                side_effect=AssertionError("strict verifier must not trust optimized identity"),
            ),
        ):
            verified = verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                self.release["dataset_id"],
            )

        self.assertEqual(verified["status"], "verified")
        oracle.assert_called_once()

    def test_fast_logical_dataset_identity_matches_frozen_oracle(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            self.assertEqual(logical_dataset_id(con), logical_dataset_id_oracle(con))

    def test_fast_logical_dataset_identity_preserves_caller_transaction(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            original = con.execute(
                "SELECT product_name FROM products WHERE item_seq='SEM-A'"
            ).fetchone()[0]
            con.execute(
                "UPDATE products SET product_name=product_name || ' transaction' "
                "WHERE item_seq='SEM-A'"
            )
            self.assertTrue(con.in_transaction)

            logical_dataset_id(con)

            self.assertTrue(con.in_transaction)
            con.rollback()
            restored = con.execute(
                "SELECT product_name FROM products WHERE item_seq='SEM-A'"
            ).fetchone()[0]
            self.assertEqual(restored, original)

    def test_fast_logical_dataset_identity_works_on_query_only_connection(self) -> None:
        uri = f"file:{self.mobile.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as con:
            con.execute("PRAGMA query_only=ON")
            self.assertEqual(logical_dataset_id(con), logical_dataset_id_oracle(con))

    def test_strict_server_verifier_threads_progress_to_frozen_oracle(self) -> None:
        progress = mock.Mock()
        with mock.patch(
            "medicine_canonical.reference_contracts.v1.logical_dataset_id_oracle",
            return_value=self.release["dataset_id"],
        ) as oracle:
            verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                self.release["dataset_id"],
                progress=progress,
            )

        oracle.assert_called_once()
        self.assertIs(oracle.call_args.kwargs["progress"], progress)

    def test_logical_dataset_identity_binds_product_link_to_runtime_semantics(self) -> None:
        review_remark = "항암제 투여로 인한 구역 및 구토의 방지에 쓰는 제품에 한함"
        reviewed = reviewed_mfds_remark("duration_caution", review_remark)
        self.assertIsNotNone(reviewed)
        assert reviewed is not None
        facts = semantic_facts_for_reviewed_remark(reviewed)
        self.assertTrue(facts)

        with sqlite3.connect(self.mobile) as con:
            con.row_factory = sqlite3.Row
            linked = con.execute(
                """SELECT l.product_rule_id,l.criterion_rule_id,i.*
                   FROM mobile_product_criterion_links l
                   JOIN ingredient_rules i ON i.id=l.criterion_rule_id
                   WHERE i.category='duration_caution'
                     AND (i.qualifier_note IS NULL OR TRIM(i.qualifier_note)='')
                   ORDER BY l.product_rule_id,l.criterion_rule_id LIMIT 1"""
            ).fetchone()
            self.assertIsNotNone(linked)
            assert linked is not None
            product_rule_id = int(linked["product_rule_id"])
            original_criterion_id = int(linked["criterion_rule_id"])
            duplicate_criterion_id = int(
                con.execute("SELECT COALESCE(MAX(id),0)+1 FROM ingredient_rules").fetchone()[0]
            )
            duplicate_source_row = int(
                con.execute("SELECT COALESCE(MAX(source_row),0)+1000 FROM ingredient_rules").fetchone()[0]
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                       id,source_dataset_key,source_row,category,sequence_text,ingredient_name,
                       ingredient_name_ko,paired_ingredient_name,rule_value,dosage_form,note,
                       qualifier_note,details
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    duplicate_criterion_id,
                    linked["source_dataset_key"],
                    duplicate_source_row,
                    linked["category"],
                    linked["sequence_text"],
                    linked["ingredient_name"],
                    linked["ingredient_name_ko"],
                    linked["paired_ingredient_name"],
                    linked["rule_value"],
                    linked["dosage_form"],
                    linked["note"],
                    review_remark,
                    linked["details"],
                ),
            )
            con.execute(
                "INSERT INTO reference_semantic_expectations(criterion_rule_id,expected_fact_count) VALUES(?,?)",
                (duplicate_criterion_id, len(facts)),
            )
            for ordinal, fact in enumerate(facts):
                con.execute(
                    """INSERT INTO reference_criterion_semantics(
                           criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
                           fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        duplicate_criterion_id,
                        ordinal,
                        fact.semantic_role,
                        fact.evaluation_mode,
                        fact.evaluator_kind,
                        fact.fallback_action,
                        fact.qualifier_type,
                        fact.display_text,
                        json.dumps(
                            fact.structured_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        fact.source_remark,
                    ),
                )

            product = con.execute(
                "SELECT item_seq,category FROM product_rules WHERE id=?",
                (product_rule_id,),
            ).fetchone()
            self.assertIsNotNone(product)
            assert product is not None
            before_review_facts = con.execute(
                """SELECT COUNT(*) FROM reference_criterion_semantics
                   WHERE criterion_rule_id=?
                     AND (evaluation_mode='review_required' OR fallback_action='review_required')""",
                (original_criterion_id,),
            ).fetchone()[0]
            self.assertEqual(before_review_facts, 0)

            before_dataset_id = logical_dataset_id(con)
            self.assertEqual(before_dataset_id, logical_dataset_id_oracle(con))
            con.execute(
                "UPDATE reference_contract_meta SET value=? WHERE key='dataset_id'",
                (before_dataset_id,),
            )
            con.commit()
            self.assertEqual(
                verify_reference_database(
                    self.mobile,
                    REFERENCE_CONTRACT_MAJOR,
                    before_dataset_id,
                )["status"],
                "verified",
            )

            con.execute(
                """UPDATE mobile_product_criterion_links
                   SET criterion_rule_id=?
                   WHERE product_rule_id=? AND criterion_rule_id=?""",
                (duplicate_criterion_id, product_rule_id, original_criterion_id),
            )
            after_review_facts = con.execute(
                """SELECT COUNT(*) FROM reference_criterion_semantics
                   WHERE criterion_rule_id=?
                     AND (evaluation_mode='review_required' OR fallback_action='review_required')""",
                (duplicate_criterion_id,),
            ).fetchone()[0]
            self.assertGreater(after_review_facts, 0)
            after_dataset_id = logical_dataset_id(con)
            self.assertEqual(after_dataset_id, logical_dataset_id_oracle(con))
            con.execute(
                "UPDATE reference_contract_meta SET value=? WHERE key='dataset_id'",
                (after_dataset_id,),
            )
            con.commit()

        self.assertNotEqual(before_dataset_id, after_dataset_id)
        self.assertEqual(
            verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                after_dataset_id,
            )["status"],
            "verified",
        )

    def test_frozen_server_verifier_ignores_diagnostic_provenance_values(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            con.execute(
                "UPDATE source_snapshots SET row_count='not-a-count' "
                "WHERE dataset_key=(SELECT dataset_key FROM source_snapshots ORDER BY dataset_key LIMIT 1)"
            )
            con.commit()

        verified = verify_reference_database(
            self.mobile,
            REFERENCE_CONTRACT_MAJOR,
            self.release["dataset_id"],
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["dataset_id"], self.release["dataset_id"])

    def test_server_verifier_rejects_replaced_runtime_product_rule_criteria_relation(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            con.execute(
                "CREATE TABLE product_rule_criteria_replacement AS "
                "SELECT * FROM product_rule_criteria WHERE 0"
            )
            con.execute("DROP VIEW product_rule_criteria")
            con.execute(
                "ALTER TABLE product_rule_criteria_replacement RENAME TO product_rule_criteria"
            )
            con.commit()

        with self.assertRaisesRegex(ValueError, "product_rule_criteria.*view"):
            verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                self.release["dataset_id"],
            )

    def test_supported_contract_window_builder_emits_every_registered_major(self) -> None:
        output = self.mobile.parent / "contract-window"

        result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(supported_contract_majors(), (1,))
        self.assertEqual(result["current_contract_major"], 1)
        self.assertEqual(result["minimum_supported_contract_major"], 1)
        self.assertEqual([entry["contract_major"] for entry in result["contracts"]], [1])
        self.assertTrue(Path(result["contracts"][0]["database"]).is_file())
        self.assertTrue(Path(result["contracts"][0]["manifest"]).is_file())

    def test_supported_contract_window_does_not_repeat_strict_identity_verification(self) -> None:
        output = self.mobile.parent / "single-identity-pass-window"
        database = output / "contract-1.sqlite"
        manifest = output / "contract-1.manifest.json"

        def export(_canonical, target, *, manifest_path, **_kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            data = b"verified-contract"
            Path(target).write_bytes(data)
            payload = {
                "contract_major": 1,
                "dataset_id": "sha256:" + "1" * 64,
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            Path(manifest_path).write_text(json.dumps(payload), encoding="utf-8")
            return payload

        strict = mock.Mock(side_effect=AssertionError("strict verifier repeated logical identity"))
        built = mock.Mock(return_value={"status": "verified"})
        implementation = ReferenceContractImplementation(
            1,
            export,
            strict,
            verify_built=built,
        )
        with (
            mock.patch(
                "medicine_canonical.reference_contracts.registry.supported_contract_majors",
                return_value=(1,),
            ),
            mock.patch(
                "medicine_canonical.reference_contracts.registry.implementation_for",
                return_value=implementation,
            ),
        ):
            result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(result["contracts"][0]["database"], str(database))
        self.assertEqual(result["contracts"][0]["manifest"], str(manifest))
        built.assert_called_once_with(database, 1, "sha256:" + "1" * 64)
        strict.assert_not_called()

    def test_publication_build_can_surface_unbuildable_previous_contract_without_hiding_it(self) -> None:
        output = self.mobile.parent / "retired-build-window"

        def fail_previous(*_args, **_kwargs):
            raise ValueError("contract 1 cannot express current semantics")

        def export_current(_canonical, database, *, manifest_path, **_kwargs):
            Path(database).write_bytes(b"contract-2")
            Path(manifest_path).write_text("{}", encoding="utf-8")
            return {
                "contract_major": 2,
                "dataset_id": "sha256:" + "2" * 64,
                "sha256": "3" * 64,
                "size_bytes": 10,
            }

        implementations = {
            1: ReferenceContractImplementation(1, fail_previous, lambda *_args: {}),
            2: ReferenceContractImplementation(2, export_current, lambda *_args: {}),
        }
        with (
            mock.patch(
                "medicine_canonical.reference_contracts.registry.supported_contract_majors",
                return_value=(1, 2),
            ),
            mock.patch(
                "medicine_canonical.reference_contracts.registry.implementation_for",
                side_effect=lambda major: implementations[major],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "cannot express"):
                build_supported_contract_window(self.canonical, output)

            result = build_supported_contract_window(
                self.canonical,
                output,
                allow_previous_failure=True,
            )

        self.assertEqual([entry["contract_major"] for entry in result["contracts"]], [2])
        self.assertEqual(result["failed_previous_contract"]["contract_major"], 1)
        self.assertEqual(result["failed_previous_contract"]["error"], "ValueError")
        self.assertFalse((output / "contract-1.sqlite").exists())
        self.assertFalse((output / "contract-1.manifest.json").exists())
        self.assertTrue((output / "contract-2.sqlite").is_file())

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