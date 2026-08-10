from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_catalog.db import catalog_stats, sync_catalog


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
                    },
                    {
                        "item_seq": "202600002",
                        "item_name": "비급여캡슐",
                        "entp_name": "다른제약",
                        "material_name": "other ingredient",
                        "cancel_date": "",
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
                "SELECT product_name,manufacturer,ingredient_name,edi_code,permit_date FROM products WHERE item_seq='202600001'"
            ).fetchone()
        self.assertEqual(row, ("테스트정10밀리그램", "테스트제약", "test ingredient", "123456789", "2026-01-02"))

        stats = catalog_stats(self.db_path)
        self.assertEqual(stats["products"], 3)
        self.assertEqual(stats["active_products"], 2)

    def test_sync_requires_service_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "service key"):
            sync_catalog(self.db_path, service_key="", progress=False)


if __name__ == "__main__":
    unittest.main()
