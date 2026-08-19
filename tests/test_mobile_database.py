from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from medicine_app.core import MedicationApp
from medicine_app.dur_status import DUR_CATEGORIES
from medicine_canonical.mobile import RUNTIME_INDEXES, build_mobile_database
from medicine_canonical.cli import main as canonical_main
from tests.test_safety_coverage import make_canonical_db


class MobileDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.mobile_db = root / "mobile.sqlite"
        self.manifest = root / "mobile.manifest.json"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_compact_snapshot_preserves_canonical_runtime_behavior_without_legacy_tables(self) -> None:
        result = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        mobile = sqlite3.connect(self.mobile_db)
        try:
            tables = {row[0] for row in mobile.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("products", tables)
            self.assertIn("mobile_product_rules", tables)
            self.assertIn("mobile_rule_sources", tables)
            self.assertIn("mobile_rule_texts", tables)
            self.assertNotIn("product_rules", tables)
            views = {row[0] for row in mobile.execute("SELECT name FROM sqlite_master WHERE type='view'")}
            self.assertIn("product_rules", views)
            self.assertIn("product_rule_criteria", views)
            self.assertNotIn("product_ingredient_criterion_links", tables)
            self.assertNotIn("product_ingredient_criterion_unresolved", tables)
            for legacy in ("product_dur", "ingredient_dur", "product_catalog", "product_code_bridge", "ingredient_aliases"):
                self.assertNotIn(legacy, tables)
            runtime_indexes = {
                row[0]
                for row in mobile.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
                )
            }
            self.assertEqual(runtime_indexes, set(RUNTIME_INDEXES))
            self.assertEqual(mobile.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            mobile.close()

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_id"], result["dataset_id"])
        self.assertEqual(manifest["sha256"], result["sha256"])
        self.assertEqual(manifest["size_bytes"], self.mobile_db.stat().st_size)

        app = MedicationApp(self.mobile_db, self.personal_db)
        person = app.create_person("온디바이스", "1990-01-01", "female", "not_pregnant", "breastfeeding")
        preview = app.preview_medication(
            person["id"], {"product_ref": "MFDS-Z", "prescription_days": 35}
        )
        self.assertEqual(preview["product"]["product_mapping_method"], "item_seq_exact")
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "exceeded")
        categories = {row["category"] for row in preview["dur_checks"]}
        supported = {category for category, _label in DUR_CATEGORIES}
        self.assertEqual(categories & supported, supported)
        self.assertEqual(len(categories & supported), 7)

    def test_mobile_build_rejects_incomplete_source_snapshot_set(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            con.execute("DELETE FROM source_snapshots WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'")
            con.commit()
        with self.assertRaisesRegex(ValueError, "canonical verification failed"):
            build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        self.assertFalse(self.mobile_db.exists())
        self.assertFalse(self.manifest.exists())

    def test_mobile_product_rules_omits_source_identity_unique_index(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with sqlite3.connect(self.mobile_db) as con:
            indexes = con.execute("PRAGMA index_list('product_rules')").fetchall()
        self.assertFalse(
            any(str(row[3]) == "u" for row in indexes),
            f"mobile product_rules unexpectedly retains a UNIQUE index: {indexes!r}",
        )

    def test_mobile_product_rules_uses_one_runtime_composite_index(self) -> None:
        build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        with sqlite3.connect(self.mobile_db) as con:
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
        with sqlite3.connect(self.mobile_db) as con:
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

    def test_mobile_build_rejects_duplicate_product_rule_source_identity(self) -> None:
        duplicate_source = self.canonical_db.with_name("canonical-duplicate-rule.sqlite")
        with sqlite3.connect(self.canonical_db) as source:
            dump = "\n".join(source.iterdump())
        unique_clause = ",\n    UNIQUE(source_dataset_key, source_row)\n)"
        self.assertIn(unique_clause, dump)
        # iterdump() orders tables by name, so ingredient_rules appears before
        # product_rules. Remove this build-time identity constraint from the
        # synthetic source tables so the fixture can represent corrupt input.
        dump = dump.replace(unique_clause, "\n)")
        with sqlite3.connect(duplicate_source) as con:
            con.executescript(dump)
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
        with sqlite3.connect(self.canonical_db) as source, sqlite3.connect(self.mobile_db) as mobile:
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

    def test_dataset_id_changes_when_mobile_data_policy_changes(self) -> None:
        first = build_mobile_database(
            self.canonical_db, self.mobile_db, manifest_path=self.manifest
        )
        second_db = self.mobile_db.with_name("mobile-next-policy.sqlite")
        second_manifest = self.manifest.with_name("mobile-next-policy.manifest.json")
        with mock.patch("medicine_canonical.mobile.MOBILE_DATA_POLICY_VERSION", "next-policy"):
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
