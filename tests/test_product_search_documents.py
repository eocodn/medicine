from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_canonical.product_search_documents import (
    materialize_product_search_documents,
    normalize_search_text,
)


class ProductSearchDocumentTest(unittest.TestCase):
    def test_normalization_is_lexical_only(self) -> None:
        self.assertEqual(normalize_search_text("  Ｂ12  10/5㎎  "), "b12 10/5mg")
        self.assertEqual(normalize_search_text("ZOLPIDEM\tTartrate"), "zolpidem tartrate")

    def test_materialization_adds_only_direct_substance_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            substance_db = Path(tmp) / "substances.sqlite"
            with closing(sqlite3.connect(substance_db)) as substances, substances:
                substances.executescript(
                    """
                    CREATE TABLE substance_names(
                        normalized_name TEXT PRIMARY KEY,
                        substance_id TEXT NOT NULL,
                        representative_name TEXT NOT NULL
                    );
                    CREATE TABLE source_identities(
                        id INTEGER PRIMARY KEY,
                        name_en TEXT,
                        name_ko TEXT,
                        normalized_name TEXT NOT NULL,
                        substance_id TEXT NOT NULL
                    );
                    INSERT INTO substance_names VALUES('gabapentin','S-GABA','Gabapentin');
                    INSERT INTO substance_names VALUES('gabapentin hydrate','S-HYDRATE','Gabapentin Hydrate');
                    INSERT INTO source_identities(name_en,name_ko,normalized_name,substance_id)
                    VALUES
                        ('Gabapentin','가바펜틴','gabapentin','S-GABA'),
                        ('Gabapentin','가바펜틴캡슐성분','gabapentin','S-GABA'),
                        ('Gabapentin Hydrate','가바펜틴수화물','gabapentin hydrate','S-HYDRATE');
                    """
                )

            con = sqlite3.connect(":memory:")
            try:
                con.executescript(
                    """
                    CREATE TABLE products(
                        item_seq TEXT PRIMARY KEY,
                        product_name TEXT NOT NULL,
                        manufacturer TEXT,
                        ingredient_text TEXT
                    );
                    INSERT INTO products VALUES(
                        'P1','메가펜틴캡슐300밀리그램(가바펜틴)','일동제약(주)','Gabapentin'
                    );
                    """
                )
                result = materialize_product_search_documents(con, substance_db)
                self.assertEqual(result["documents"], 1)
                self.assertEqual(result["index_rows"], 1)
                row = con.execute(
                    """SELECT normalized_product_name,normalized_manufacturer,
                              normalized_ingredient_names
                       FROM product_search_documents WHERE item_seq='P1'"""
                ).fetchone()
                assert row is not None
                self.assertEqual(
                    row[0], "메가펜틴캡슐300밀리그램(가바펜틴)"
                )
                self.assertEqual(row[1], "일동제약(주)")
                self.assertIn("\ngabapentin\n", row[2])
                self.assertIn("\n가바펜틴\n", row[2])
                self.assertIn("\n가바펜틴캡슐성분\n", row[2])
                self.assertNotIn("가바펜틴수화물", row[2])
                indexed = con.execute(
                    """SELECT d.item_seq
                       FROM product_search_fts f
                       JOIN product_search_documents d ON d.rowid=f.rowid
                       WHERE product_search_fts MATCH '가바펜틴'"""
                ).fetchall()
                self.assertEqual(indexed, [("P1",)])
            finally:
                con.close()

    def test_unresolved_permit_component_keeps_raw_ingredient_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            substance_db = Path(tmp) / "substances.sqlite"
            with closing(sqlite3.connect(substance_db)) as substances, substances:
                substances.executescript(
                    """
                    CREATE TABLE substance_names(
                        normalized_name TEXT PRIMARY KEY,
                        substance_id TEXT NOT NULL,
                        representative_name TEXT NOT NULL
                    );
                    CREATE TABLE source_identities(
                        id INTEGER PRIMARY KEY,
                        name_en TEXT,
                        name_ko TEXT,
                        normalized_name TEXT NOT NULL,
                        substance_id TEXT NOT NULL
                    );
                    """
                )
            con = sqlite3.connect(":memory:")
            try:
                con.executescript(
                    """
                    CREATE TABLE products(
                        item_seq TEXT PRIMARY KEY,
                        product_name TEXT NOT NULL,
                        manufacturer TEXT,
                        ingredient_text TEXT
                    );
                    INSERT INTO products VALUES('P2','미지제품','제조사','Mystery Compound');
                    """
                )
                materialize_product_search_documents(con, substance_db)
                aliases = con.execute(
                    "SELECT normalized_ingredient_names FROM product_search_documents WHERE item_seq='P2'"
                ).fetchone()[0]
                self.assertIn("\nmystery compound\n", aliases)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()