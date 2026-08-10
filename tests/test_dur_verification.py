from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from medicine_dur.verification import REQUIRED_HEADERS, REQUIRED_SOURCE_KEYS, verify_database


class DurVerificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "dur.sqlite"
        self.sources = root / "sources"
        self.sources.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_complete_db(self) -> None:
        con = sqlite3.connect(self.db)
        con.executescript(
            """
            CREATE TABLE source_files (
                id INTEGER PRIMARY KEY,
                dataset_key TEXT NOT NULL UNIQUE,
                source_kind TEXT NOT NULL,
                category TEXT NOT NULL,
                source_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                row_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE product_dur (
                id INTEGER PRIMARY KEY,
                dataset_key TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                category TEXT NOT NULL,
                ingredient_name TEXT,
                ingredient_code TEXT,
                product_name TEXT,
                product_code TEXT,
                paired_ingredient_name TEXT,
                paired_ingredient_code TEXT,
                paired_product_name TEXT,
                paired_product_code TEXT,
                rule_value TEXT,
                details TEXT,
                notice_no TEXT,
                notice_date TEXT
            );
            CREATE TABLE ingredient_dur (
                id INTEGER PRIMARY KEY,
                dataset_key TEXT NOT NULL,
                source_row INTEGER NOT NULL,
                category TEXT NOT NULL,
                ingredient_name TEXT,
                ingredient_name_ko TEXT,
                paired_ingredient_name TEXT,
                rule_value TEXT,
                dosage_form TEXT,
                note TEXT,
                details TEXT,
                sequence_text TEXT
            );
            """
        )
        for index, key in enumerate(REQUIRED_SOURCE_KEYS, 1):
            kind, category = key.split(":", 1)
            source_path = self.sources / f"source-{index}"
            source_bytes = f"{key}\nfixture\n".encode("utf-8")
            source_path.write_bytes(source_bytes)
            con.execute(
                """INSERT INTO source_files(
                    dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    key, kind, category, str(source_path), hashlib.sha256(source_bytes).hexdigest(), len(source_bytes), 1,
                    json.dumps({"title": "fixture 2026.08.01", "header": sorted(REQUIRED_HEADERS[key])}, ensure_ascii=False),
                ),
            )
            if kind == "product":
                con.execute(
                    """INSERT INTO product_dur(
                        dataset_key,source_row,category,ingredient_name,product_name,product_code,notice_date
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (key, 1, category, "fixture", "fixture", f"P-{index}", "2026-08-01"),
                )
            else:
                con.execute(
                    """INSERT INTO ingredient_dur(
                        dataset_key,source_row,category,ingredient_name,sequence_text
                    ) VALUES(?,?,?,?,?)""",
                    (key, 1, category, "fixture", "1"),
                )
        con.commit()
        con.close()

    def test_complete_recent_dataset_passes_release_gate(self) -> None:
        self._make_complete_db()

        result = verify_database(self.db, max_age_days=730, as_of=date(2026, 8, 11))

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["integrity_check"], "ok")
        self.assertTrue(result["dataset_id"].startswith("sha256:"))
        self.assertEqual(result["issues"], [])

    def test_missing_or_stale_source_fails_release_gate(self) -> None:
        self._make_complete_db()
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM source_files WHERE dataset_key='ingredient:lactation_caution'")
        con.execute(
            "UPDATE source_files SET metadata_json=? WHERE dataset_key='ingredient:elderly_caution'",
            (json.dumps({"title": "fixture 2020.01.01", "header": sorted(REQUIRED_HEADERS["ingredient:elderly_caution"])}, ensure_ascii=False),),
        )
        con.execute(
            "UPDATE source_files SET metadata_json=? WHERE dataset_key='product:dose_caution'",
            (json.dumps({"header": ["제품명", "제품코드"]}, ensure_ascii=False),),
        )
        source_path = Path(con.execute(
            "SELECT source_path FROM source_files WHERE dataset_key='product:duration_caution'"
        ).fetchone()[0])
        con.commit()
        con.close()
        source_path.write_text("tampered", encoding="utf-8")

        result = verify_database(self.db, max_age_days=730, as_of=date(2026, 8, 11))

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("missing source" in issue for issue in result["issues"]))
        self.assertTrue(any("source stale" in issue for issue in result["issues"]))
        self.assertTrue(any("missing_headers" in issue for issue in result["issues"]))
        self.assertTrue(any("source hash mismatch" in issue for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()
