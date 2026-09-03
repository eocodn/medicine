from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from medicine_reference.reference_contracts.v1 import REFERENCE_CONTRACT_MAJOR, verify_reference_database
from medicine_canonical.mobile import RUNTIME_INDEXES, build_mobile_database
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.product_search_documents import materialize_product_search_fts
from tests.canonical_fixture_support import make_canonical_db


class MobileDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.mobile_db = root / "mobile.sqlite"
        self.manifest = root / "mobile.manifest.json"
        make_canonical_db(self.canonical_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mobile_build_resumes_runtime_copy_from_input_bound_checkpoint(self) -> None:
        events: list[dict[str, object]] = []
        with mock.patch(
            "medicine_canonical.mobile._populate_compact_product_rules",
            side_effect=RuntimeError("compact interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "compact interrupted"):
                build_mobile_database(
                    self.canonical_db,
                    self.mobile_db,
                    manifest_path=self.manifest,
                    progress=events.append,
                )

        checkpoint = self.mobile_db.with_name(
            self.mobile_db.name + ".build.checkpoint.json"
        )
        temporary = self.mobile_db.with_name(self.mobile_db.name + ".tmp")
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(temporary.is_file())
        self.assertTrue(
            any(
                event.get("job") == "mobile-reference-build"
                and event.get("status") == "checkpoint"
                and event.get("phase") == "runtime_copied"
                for event in events
            )
        )

        with mock.patch(
            "medicine_canonical.mobile.verify_canonical_database",
            side_effect=AssertionError("canonical verification must not rerun"),
        ), mock.patch(
            "medicine_canonical.mobile.COPIED_RUNTIME_TABLES",
            ("missing_if_copy_restarts",),
        ):
            result = build_mobile_database(
                self.canonical_db,
                self.mobile_db,
                manifest_path=self.manifest,
                progress=events.append,
            )

        self.assertEqual(result["contract_major"], REFERENCE_CONTRACT_MAJOR)
        clean_db = self.mobile_db.with_name("mobile-clean.sqlite")
        clean_manifest = self.manifest.with_name("mobile-clean.manifest.json")
        clean = build_mobile_database(
            self.canonical_db,
            clean_db,
            manifest_path=clean_manifest,
        )
        self.assertEqual(result["dataset_id"], clean["dataset_id"])
        self.assertEqual(result["sha256"], clean["sha256"])
        self.assertEqual(self.mobile_db.read_bytes(), clean_db.read_bytes())
        self.assertFalse(checkpoint.exists())
        self.assertFalse(temporary.exists())
        self.assertTrue(self.mobile_db.is_file())
        self.assertTrue(self.manifest.is_file())

    def test_mobile_build_repairs_manifest_after_database_commit_interruption(self) -> None:
        with mock.patch(
            "medicine_canonical.mobile.write_manifest_atomic",
            side_effect=RuntimeError("manifest write interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "manifest write interrupted"):
                build_mobile_database(
                    self.canonical_db,
                    self.mobile_db,
                    manifest_path=self.manifest,
                )

        checkpoint = self.mobile_db.with_name(
            self.mobile_db.name + ".build.checkpoint.json"
        )
        temporary = self.mobile_db.with_name(self.mobile_db.name + ".tmp")
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(self.mobile_db.is_file())
        self.assertFalse(temporary.exists())
        self.assertFalse(self.manifest.exists())

        with mock.patch(
            "medicine_canonical.mobile.verify_canonical_database",
            side_effect=AssertionError("canonical verification must not rerun"),
        ), mock.patch(
            "medicine_canonical.mobile._populate_compact_product_rules",
            side_effect=AssertionError("materialization must not rerun"),
        ), mock.patch(
            "medicine_canonical.mobile.materialize_product_search_fts",
            side_effect=AssertionError("search materialization must not rerun"),
        ):
            result = build_mobile_database(
                self.canonical_db,
                self.mobile_db,
                manifest_path=self.manifest,
            )

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["sha256"], result["sha256"])
        self.assertEqual(manifest["dataset_id"], result["dataset_id"])
        self.assertFalse(checkpoint.exists())

    def test_compact_snapshot_preserves_frozen_contract(self) -> None:
        result = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        mobile = sqlite3.connect(self.mobile_db)
        try:
            tables = {row[0] for row in mobile.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("products", tables)
            self.assertIn("product_search_documents", tables)
            self.assertIn("product_search_fts", tables)
            self.assertIn("mobile_product_rules", tables)
            self.assertIn("mobile_rule_sources", tables)
            self.assertIn("mobile_rule_texts", tables)
            self.assertNotIn("product_rules", tables)
            views = {row[0] for row in mobile.execute("SELECT name FROM sqlite_master WHERE type='view'")}
            self.assertIn("product_rules", views)
            self.assertIn("product_rule_criteria", views)
            self.assertNotIn("product_ingredient_criterion_links", tables)
            self.assertNotIn("product_ingredient_criterion_unresolved", tables)
            runtime_indexes = {
                row[0]
                for row in mobile.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
                )
            }
            self.assertEqual(runtime_indexes, set(RUNTIME_INDEXES))
            self.assertEqual(
                mobile.execute("SELECT COUNT(*) FROM product_search_documents").fetchone()[0],
                mobile.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            )
            self.assertEqual(
                mobile.execute("SELECT COUNT(*) FROM product_search_fts").fetchone()[0],
                mobile.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            )
            self.assertEqual(mobile.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            mobile.close()

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_id"], result["dataset_id"])
        self.assertEqual(manifest["sha256"], result["sha256"])
        self.assertEqual(manifest["size_bytes"], self.mobile_db.stat().st_size)
        verified = verify_reference_database(
            self.mobile_db, REFERENCE_CONTRACT_MAJOR, result["dataset_id"]
        )
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["dataset_id"], result["dataset_id"])

        rust_verified = subprocess.run(
            [
                "medicine-agentctl", "reference-verify",
                "--reference-db", str(self.mobile_db),
                "--contract-major", str(REFERENCE_CONTRACT_MAJOR),
                "--dataset-id", result["dataset_id"],
                "--json",
            ],
            check=True, capture_output=True, text=True,
        )
        rust_verified_payload = json.loads(rust_verified.stdout)
        self.assertEqual(rust_verified_payload["status"], 200)
        self.assertEqual(rust_verified_payload["body"]["status"], "verified")
        self.assertEqual(rust_verified_payload["body"]["dataset_id"], result["dataset_id"])

        rust_product = subprocess.run(
            [
                "medicine-agentctl", "product",
                "--canonical-db", str(self.mobile_db),
                "--product-ref", "MFDS-Z",
                "--json",
            ],
            check=True, capture_output=True, text=True,
        )
        product_payload = json.loads(rust_product.stdout)
        self.assertEqual(product_payload["status"], 200)
        self.assertEqual(product_payload["body"]["catalog_item_seq"], "MFDS-Z")
        self.assertEqual(product_payload["body"]["product_mapping_method"], "item_seq_exact")

        rust_safety = subprocess.run(
            [
                "medicine-agentctl", "safety-basis",
                "--canonical-db", str(self.mobile_db),
                "--product-ref", "MFDS-Z",
                "--person", json.dumps({
                    "birth_date": "1990-01-01",
                    "sex": "male",
                    "pregnancy_status": "not_applicable",
                }, separators=(",", ":")),
                "--draft", json.dumps({
                    "prescription_days": 35,
                    "start_date": "2026-08-20",
                }, separators=(",", ":")),
                "--json",
            ],
            check=True, capture_output=True, text=True,
        )
        safety_payload = json.loads(rust_safety.stdout)
        self.assertEqual(safety_payload["status"], 200)
        self.assertEqual(
            safety_payload["body"]["quantitative_checks"]["duration"]["result"],
            "exceeded",
        )
        self.assertEqual(
            safety_payload["body"]["quantitative_checks"]["duration"]["maximum_days"],
            28,
        )

    def test_frozen_contract_uses_signed_dataset_identity_and_ignores_diagnostic_provenance(self) -> None:
        result = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        first = verify_reference_database(
            self.mobile_db, REFERENCE_CONTRACT_MAJOR, result["dataset_id"]
        )
        self.assertEqual(first["status"], "verified")

        with closing(sqlite3.connect(self.mobile_db)) as con, con:
            con.execute(
                "UPDATE source_snapshots SET sha256=?, row_count=0 "
                "WHERE dataset_key=(SELECT dataset_key FROM source_snapshots ORDER BY dataset_key LIMIT 1)",
                ("f" * 64,),
            )
            con.commit()

        second = verify_reference_database(
            self.mobile_db, REFERENCE_CONTRACT_MAJOR, result["dataset_id"]
        )
        self.assertEqual(second["status"], "verified")
        self.assertEqual(second["dataset_id"], first["dataset_id"])

    def test_mobile_build_rejects_incomplete_source_snapshot_set(self) -> None:
        with closing(sqlite3.connect(self.canonical_db)) as con, con:
            con.execute("DELETE FROM source_snapshots WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'")
            con.commit()
        with self.assertRaisesRegex(ValueError, "canonical verification failed"):
            build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        self.assertFalse(self.mobile_db.exists())
        self.assertFalse(self.manifest.exists())

    def test_mobile_build_does_not_mutate_canonical_search_index(self) -> None:
        with closing(sqlite3.connect(self.canonical_db)) as con:
            before = con.execute("SELECT COUNT(*) FROM product_search_fts").fetchone()[0]

        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)

        with closing(sqlite3.connect(self.canonical_db)) as con:
            after = con.execute("SELECT COUNT(*) FROM product_search_fts").fetchone()[0]
            exists = con.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='product_search_fts'"
            ).fetchone()[0]
        self.assertEqual(exists, 1)
        self.assertEqual(after, before)

    def test_mobile_product_rules_omits_source_identity_unique_index(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with closing(sqlite3.connect(self.mobile_db)) as con, con:
            indexes = con.execute("PRAGMA index_list('mobile_product_rules')").fetchall()
        self.assertFalse(
            any(bool(row[2]) for row in indexes),
            f"mobile_product_rules unexpectedly retains a UNIQUE index: {indexes!r}",
        )

    def test_mobile_product_rules_uses_one_runtime_composite_index(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with closing(sqlite3.connect(self.mobile_db)) as con, con:
            columns = [
                str(row[2])
                for row in con.execute("PRAGMA index_info('idx_product_rules_runtime')")
            ]
            self.assertEqual(columns, ["item_seq", "category_text_id", "paired_item_seq"])
            pair = con.execute(
                "SELECT item_seq,category,paired_item_seq FROM product_rules "
                "WHERE paired_item_seq IS NOT NULL LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(pair)
            assert pair is not None
            item_plan = " ".join(
                str(row[3])
                for row in con.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM product_rules "
                    "WHERE item_seq=? AND category=?",
                    (pair[0], pair[1]),
                )
            )
            pair_plan = " ".join(
                str(row[3])
                for row in con.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM product_rules "
                    "WHERE item_seq=? AND category=? AND paired_item_seq=?",
                    pair,
                )
            )
        self.assertIn("idx_product_rules_runtime", item_plan)
        self.assertIn("idx_product_rules_runtime", pair_plan)

    def test_mobile_product_rules_uses_compact_physical_storage_with_compatibility_view(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with closing(sqlite3.connect(self.mobile_db)) as con, con:
            objects = {
                str(row[0]): str(row[1])
                for row in con.execute(
                    "SELECT name,type FROM sqlite_master "
                    "WHERE name IN ('product_rules','mobile_product_rules','mobile_rule_sources','mobile_rule_texts')"
                )
            }
            physical_columns = {
                str(row[1])
                for row in con.execute("PRAGMA table_info('mobile_product_rules')")
            }
            runtime_columns = [
                str(row[1])
                for row in con.execute("PRAGMA table_info('product_rules')")
            ]
        self.assertEqual(objects["product_rules"], "view")
        self.assertEqual(objects["mobile_product_rules"], "table")
        self.assertEqual(objects["mobile_rule_sources"], "table")
        self.assertEqual(objects["mobile_rule_texts"], "table")
        self.assertIn("category_text_id", physical_columns)
        self.assertIn("details_text_id", physical_columns)
        self.assertNotIn("category", physical_columns)
        self.assertNotIn("details", physical_columns)
        self.assertEqual(
            runtime_columns,
            [
                "id", "source_dataset_key", "source_row", "category", "item_seq",
                "ingredient_code", "ingredient_name", "ingredient_name_en",
                "paired_item_seq", "paired_ingredient_code", "paired_ingredient_name",
                "paired_ingredient_name_en", "effect_name", "dosage_form", "details",
                "notification_date", "change_date",
            ],
        )

    def test_mobile_criterion_links_use_compact_codes_with_compatibility_view(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with closing(sqlite3.connect(self.mobile_db)) as con, con:
            objects = {
                str(row[0]): str(row[1])
                for row in con.execute(
                    "SELECT name,type FROM sqlite_master "
                    "WHERE name IN ('product_criterion_links','mobile_product_criterion_links')"
                )
            }
            physical_columns = {
                str(row[1])
                for row in con.execute("PRAGMA table_info('mobile_product_criterion_links')")
            }
            runtime_columns = [
                str(row[1])
                for row in con.execute("PRAGMA table_info('product_criterion_links')")
            ]
        self.assertEqual(objects["product_criterion_links"], "view")
        self.assertEqual(objects["mobile_product_criterion_links"], "table")
        self.assertIn("match_method_code", physical_columns)
        self.assertIn("pair_orientation_code", physical_columns)
        self.assertNotIn("match_method", physical_columns)
        self.assertNotIn("pair_orientation", physical_columns)
        self.assertEqual(
            runtime_columns,
            ["product_rule_id", "criterion_rule_id", "match_method", "pair_orientation"],
        )

    def test_mobile_build_rejects_duplicate_product_rule_source_identity(self) -> None:
        duplicate_source = self.canonical_db.with_name("canonical-duplicate-rule.sqlite")
        with closing(sqlite3.connect(self.canonical_db)) as source, source:
            # Python's iterdump serializes FTS5 virtual/shadow tables through
            # writable sqlite_master statements which are not safely replayable.
            # The search document table is authoritative, so omit the accelerator
            # from this corruption fixture and deterministically rebuild it below.
            dump = "\n".join(
                line for line in source.iterdump() if "product_search_fts" not in line
            )
        unique_clause = ",\n    UNIQUE(source_dataset_key, source_row)\n)"
        self.assertIn(unique_clause, dump)
        # iterdump() orders tables by name, so ingredient_rules appears before
        # product_rules. Remove this build-time identity constraint from the
        # synthetic source tables so the fixture can represent corrupt input.
        dump = dump.replace(unique_clause, "\n)")
        with closing(sqlite3.connect(duplicate_source)) as con, con:
            con.executescript(dump)
            materialize_product_search_fts(con)
            columns = [
                str(row[1])
                for row in con.execute("PRAGMA table_info('product_rules')")
            ]
            source_row = con.execute(
                "SELECT * FROM product_rules ORDER BY id LIMIT 1"
            ).fetchone()
            assert source_row is not None
            duplicate = list(source_row)
            duplicate[columns.index("id")] = int(
                con.execute("SELECT MAX(id) FROM product_rules").fetchone()[0]
            ) + 1
            placeholders = ",".join("?" for _ in columns)
            con.execute(
                f"INSERT INTO product_rules ({','.join(columns)}) VALUES ({placeholders})",
                duplicate,
            )
            con.commit()

        with self.assertRaisesRegex(ValueError, "product_rules source identity is not unique"):
            build_mobile_database(duplicate_source, self.mobile_db, manifest_path=self.manifest)
        self.assertFalse(self.mobile_db.exists())
        self.assertFalse(self.manifest.exists())

    def test_mobile_preserves_product_rule_ids_and_criterion_links(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with (
            closing(sqlite3.connect(self.canonical_db)) as source,
            source,
            closing(sqlite3.connect(self.mobile_db)) as mobile,
            mobile,
        ):
            source_rules = source.execute(
                "SELECT * FROM product_rules ORDER BY id"
            ).fetchall()
            mobile_rules = mobile.execute(
                "SELECT * FROM product_rules ORDER BY id"
            ).fetchall()
            source_links = source.execute(
                "SELECT product_rule_id,criterion_rule_id,match_method,pair_orientation "
                "FROM product_criterion_links ORDER BY product_rule_id,criterion_rule_id"
            ).fetchall()
            mobile_links = mobile.execute(
                "SELECT product_rule_id,criterion_rule_id,match_method,pair_orientation "
                "FROM product_criterion_links ORDER BY product_rule_id,criterion_rule_id"
            ).fetchall()
        self.assertEqual(mobile_rules, source_rules)
        self.assertEqual(mobile_links, source_links)

    def test_dataset_id_is_stable_across_physical_mobile_policy_changes(self) -> None:
        first = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        second_db = self.mobile_db.with_name("mobile-next-policy.sqlite")
        second_manifest = self.manifest.with_name("mobile-next-policy.manifest.json")
        second = build_mobile_database(
            self.canonical_db,
            second_db,
            manifest_path=second_manifest,
            physical_policy_version="next-policy",
        )

        self.assertEqual(first["dataset_id"], second["dataset_id"])
        self.assertEqual(first["contract_major"], 1)
        self.assertEqual(second["contract_major"], 1)
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_dataset_id_changes_when_contract_visible_semantics_change(self) -> None:
        first = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        with closing(sqlite3.connect(self.canonical_db)) as con, con:
            con.execute(
                "UPDATE ingredient_rules SET details=COALESCE(details,'') || ' semantic-change' "
                "WHERE id=(SELECT MIN(id) FROM ingredient_rules)"
            )
            con.commit()
        second_db = self.mobile_db.with_name("mobile-semantic-change.sqlite")
        second_manifest = self.manifest.with_name("mobile-semantic-change.manifest.json")
        second = build_mobile_database(
            self.canonical_db, second_db, manifest_path=second_manifest
        )

        self.assertNotEqual(first["dataset_id"], second["dataset_id"])

    def test_canonical_cli_builds_mobile_snapshot(self) -> None:
        other = self.mobile_db.with_name("mobile-cli.sqlite")
        manifest = self.manifest.with_name("mobile-cli.manifest.json")
        code = canonical_main([
            "mobile-build", "--db", str(self.canonical_db), "--output", str(other),
            "--manifest", str(manifest), "--json",
        ])
        self.assertEqual(code, 0)
        self.assertTrue(other.is_file())
        self.assertTrue(manifest.is_file())


if __name__ == "__main__":
    unittest.main()
