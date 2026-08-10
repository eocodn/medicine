from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_catalog.db import catalog_stats, sync_catalog, upgrade_catalog
from medicine_catalog.status_sources import classify_probe_response


class CatalogSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "catalog.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sync_builds_searchable_catalog_atomically(self) -> None:
        pages = {
            1: (
                [
                    {
                        "ITEM_SEQ": "202600001",
                        "ITEM_NAME": "테스트정10밀리그램",
                        "ENTP_NAME": "테스트제약",
                        "MAIN_ITEM_INGR": "test ingredient",
                        "EDI_CODE": "123456789",
                        "ITEM_PERMIT_DATE": "20260102",
                        "CANCEL_NAME": "정상",
                    },
                    {
                        "item_seq": "202600002",
                        "item_name": "비급여캡슐",
                        "entp_name": "다른제약",
                        "material_name": "other ingredient",
                        "cancel_date": "",
                        "cancel_name": "정상",
                    },
                ],
                3,
            ),
            2: (
                [
                    {
                        "ITEM_SEQ": "202600003",
                        "ITEM_NAME": "취소약",
                        "ENTP_NAME": "테스트제약",
                        "CANCEL_DATE": "20260201",
                        "CANCEL_NAME": "취하",
                    }
                ],
                3,
            ),
        }

        def fetch_page(page: int, page_size: int):
            self.assertEqual(page_size, 2)
            return pages[page]

        result = sync_catalog(
            self.db_path,
            service_key="test-key",
            page_size=2,
            progress=False,
            fetch_page=fetch_page,
        )

        self.assertEqual(result["products"], 3)
        self.assertTrue(self.db_path.exists())
        self.assertFalse(self.db_path.with_name("catalog.sqlite.tmp").exists())
        self.assertFalse(self.db_path.with_name("catalog.sqlite.checkpoint.json").exists())

        with closing(sqlite3.connect(self.db_path)) as con:
            row = con.execute(
                "SELECT product_name,manufacturer,ingredient_name,edi_code,permit_date,cancel_name,permit_status "
                "FROM products WHERE item_seq='202600001'"
            ).fetchone()
        self.assertEqual(
            row,
            ("테스트정10밀리그램", "테스트제약", "test ingredient", "123456789", "2026-01-02", "정상", "active"),
        )

        stats = catalog_stats(self.db_path)
        self.assertEqual(stats["products"], 3)
        self.assertEqual(stats["active_products"], 2)
        self.assertEqual(stats["permit_status_counts"], {"active": 2, "withdrawn": 1})

    def test_upgrade_backfills_permit_status_from_existing_raw_json(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as con:
            con.executescript(
                """
                CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE products (
                    item_seq TEXT PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    manufacturer TEXT,
                    ingredient_name TEXT,
                    dosage_form TEXT,
                    edi_code TEXT,
                    permit_date TEXT,
                    cancel_date TEXT,
                    source TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );
                """
            )
            rows = [
                ("A", "정상약", None, None, None, None, "2020-01-01", None, "mfds", '{"CANCEL_NAME":"정상"}'),
                ("B", "만료약", None, None, None, None, "2020-01-01", "2025-01-01", "mfds", '{"CANCEL_NAME":"유효기간만료"}'),
                ("C", "행정취소약", None, None, None, None, "2020-01-01", "2025-02-01", "mfds", '{"CANCEL_NAME":"행정(취소)"}'),
                ("D", "폐업약", None, None, None, None, "2020-01-01", "2025-03-01", "mfds", '{"CANCEL_NAME":"폐업"}'),
            ]
            con.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()

        result = upgrade_catalog(self.db_path)

        self.assertTrue(result["upgraded"])
        self.assertEqual(
            result["permit_status_counts"],
            {"active": 1, "business_closed": 1, "canceled": 1, "expired": 1},
        )
        with closing(sqlite3.connect(self.db_path)) as con:
            rows = con.execute("SELECT item_seq,cancel_name,permit_status FROM products ORDER BY item_seq").fetchall()
        self.assertEqual(
            rows,
            [
                ("A", "정상", "active"),
                ("B", "유효기간만료", "expired"),
                ("C", "행정(취소)", "canceled"),
                ("D", "폐업", "business_closed"),
            ],
        )

    def test_sync_requires_service_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "service key"):
            sync_catalog(self.db_path, service_key="", progress=False)

    def test_status_source_probe_classifies_permission_and_success(self) -> None:
        denied = classify_probe_response(
            403,
            '{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}}',
        )
        self.assertEqual(denied["status"], "permission_required")

        success = classify_probe_response(
            200,
            '{"response":{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE."}}}',
        )
        self.assertEqual(success["status"], "available")


if __name__ == "__main__":
    unittest.main()
