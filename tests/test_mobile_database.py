from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import MedicationApp
from medicine_dur.mobile import build_mobile_database
from medicine_dur.verification import dataset_manifest
from tests.test_safety_coverage import make_catalog_db, make_dur_db


class MobileDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        self.mobile_db = root / "mobile.sqlite"
        self.manifest = root / "mobile.manifest.json"
        self.personal_db = root / "personal.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_compact_snapshot_preserves_dataset_identity_and_runtime_behavior(self) -> None:
        result = build_mobile_database(
            self.dur_db,
            self.catalog_db,
            self.mobile_db,
            manifest_path=self.manifest,
            require_verified_source=False,
        )

        source = sqlite3.connect(self.dur_db)
        mobile = sqlite3.connect(self.mobile_db)
        try:
            self.assertEqual(dataset_manifest(source)["dataset_id"], dataset_manifest(mobile)["dataset_id"])
            product_columns = {row[1] for row in mobile.execute("PRAGMA table_info(product_dur)")}
            catalog_columns = {row[1] for row in mobile.execute("PRAGMA table_info(products)")}
            self.assertNotIn("ingredient_code", product_columns)
            self.assertNotIn("paired_ingredient_name", product_columns)
            self.assertNotIn("raw_json", catalog_columns)
            self.assertEqual(mobile.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            source.close()
            mobile.close()

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["dataset_id"], result["dataset_id"])
        self.assertEqual(manifest["sha256"], result["sha256"])
        self.assertEqual(manifest["size_bytes"], self.mobile_db.stat().st_size)

        app = MedicationApp(self.mobile_db, self.personal_db, self.mobile_db)
        person = app.create_person("온디바이스", "1990-01-01", "male", "not_applicable")
        preview = app.preview_medication(
            person["id"],
            {"product_ref": "MFDS-Z", "prescription_days": 35, "start_date": "2026-08-11"},
        )
        self.assertEqual(preview["product"]["product_name"], "졸피뎀제품")
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "exceeded")


if __name__ == "__main__":
    unittest.main()
