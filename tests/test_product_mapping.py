from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.products import ProductRepository


def make_dur_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE product_catalog (
            product_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            ingredient_code TEXT,
            ingredient_name TEXT
        );
        CREATE TABLE ingredient_dur (
            ingredient_name TEXT,
            paired_ingredient_name TEXT
        );
        """
    )
    con.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Methotrexate",))
    con.commit()
    con.close()


def make_catalog_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE products (
            item_seq TEXT PRIMARY KEY, product_name TEXT NOT NULL, manufacturer TEXT,
            ingredient_name TEXT, dosage_form TEXT, edi_code TEXT, permit_date TEXT,
            cancel_date TEXT, source TEXT NOT NULL, raw_json TEXT NOT NULL,
            cancel_name TEXT, permit_status TEXT
        )"""
    )
    con.commit()
    con.close()


class ProductMappingStrengthFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.dur_db = root / "dur.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.products = ProductRepository(self.dur_db, self.catalog_db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _insert_catalog_product(self, item_seq: str, product_name: str) -> None:
        con = sqlite3.connect(self.catalog_db)
        con.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_seq, product_name, "제약", "Methotrexate", None, None,
                "1990-07-03", None, "정상", "active", "fixture", "{}",
            ),
        )
        con.commit()
        con.close()

    def test_unique_name_and_strength_annotated_ingredient_recovers_missing_edi_link(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-MTX",
                "네오메토주(메토트렉세이트)(수출명:MethotrexateKalbe)_(0.5g/20mL)",
                "ING-MTX",
                "methotrexate   0.5g(25mg/mL)",
            ),
        )
        dur.commit()
        dur.close()
        self._insert_catalog_product(
            "MFDS-MTX", "네오메토주(메토트렉세이트)(수출명:MethotrexateKalbe)"
        )

        product = self.products.get("MFDS-MTX")

        self.assertEqual(product["product_code"], "P-MTX")
        self.assertEqual(product["product_mapping_status"], "matched")
        self.assertEqual(
            product["product_mapping_method"],
            "normalized_name_ingredient_strength_unique",
        )
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["methotrexate"])

    def test_multiple_strength_annotated_product_codes_remain_ambiguous(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("P-MTX-1", "동일메토주_(0.5g/20mL)", "ING-M1", "methotrexate 0.5g(25mg/mL)"),
                ("P-MTX-2", "동일메토주_(1g/40mL)", "ING-M2", "methotrexate 1g(25mg/mL)"),
            ],
        )
        dur.commit()
        dur.close()
        self._insert_catalog_product("MFDS-MTX-DUP", "동일메토주")

        product = self.products.get("MFDS-MTX-DUP")

        self.assertIsNone(product["product_code"])
        self.assertEqual(product["product_mapping_status"], "ambiguous")
        self.assertEqual(
            product["product_mapping_method"],
            "normalized_name_ingredient_strength_ambiguous",
        )
        self.assertEqual(product["matched_product_codes"], ["P-MTX-1", "P-MTX-2"])


if __name__ == "__main__":
    unittest.main()
