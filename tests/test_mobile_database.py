from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from medicine_app.core import MedicationApp
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
            self.assertIn("product_rules", tables)
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
        self.assertNotIn(
            "lactation_caution", {row["category"] for row in preview["dur_checks"]}
        )

    def test_mobile_build_rejects_incomplete_source_snapshot_set(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            con.execute("DELETE FROM source_snapshots WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'")
            con.commit()
        with self.assertRaisesRegex(ValueError, "canonical verification failed"):
            build_mobile_database(self.canonical_db, self.mobile_db, manifest_path=self.manifest)
        self.assertFalse(self.mobile_db.exists())
        self.assertFalse(self.manifest.exists())

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
