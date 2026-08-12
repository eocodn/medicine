from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.ingredient_aliases import (
    derive_validated_ingredient_aliases,
    materialize_validated_ingredient_aliases,
)
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
        CREATE TABLE product_dur (
            dataset_key TEXT,
            source_row INTEGER,
            ingredient_name TEXT,
            ingredient_code TEXT,
            product_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_code TEXT,
            paired_product_code TEXT
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

    def test_terminal_strength_annotation_preserves_exact_ingredient_identity(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Amlodipine",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-AMLO-STRENGTH", "암로디핀정", "ING-AMLO", "amlodipine 5mg"),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-AMLO-STRENGTH", "암로디핀정", "제약", "Amlodipine", None,
                "P-AMLO-STRENGTH", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-AMLO-STRENGTH")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["amlodipine"])
        self.assertEqual(product["ingredient_mapping_method"], "product_code_exact")

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


    def test_materialized_validated_alias_recovers_catalog_salt_name_without_product_link(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Tamoxifen",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-TAM-EVIDENCE", "타목시펜근거정", "ING-TAM", "tamoxifen"),
        )
        dur.commit()
        dur.close()

        catalog = sqlite3.connect(self.catalog_db)
        catalog.executemany(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "MFDS-TAM-EVIDENCE", "타목시펜근거정", "제약", "Tamoxifen Citrate", None,
                    "P-TAM-EVIDENCE", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-TAM-NO-EDI", "타목시펜별칭정", "제약", "Tamoxifen Citrate", None,
                    None, "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        self.assertEqual(result["validated_aliases"], 1)

        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-TAM-NO-EDI")

        self.assertEqual(product["product_mapping_status"], "not_matched")
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")
        self.assertEqual(product["safety_ingredients"], ["tamoxifen"])

    def test_explicit_dur_as_active_moiety_validates_alias(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Clopidogrel",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-CLOP", "클로피도그렐근거정", "ING-CLOP", "Clopidogrel Bisulfate (as Clopidogrel)"),
        )
        dur.execute(
            """INSERT INTO product_dur(
                dataset_key,source_row,ingredient_name,ingredient_code,product_code,
                paired_ingredient_name,paired_ingredient_code,paired_product_code
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "product:combination_contraindication", 1,
                "Clopidogrel Bisulfate (as Clopidogrel)", "ING-CLOP", "P-CLOP",
                None, None, None,
            ),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.executemany(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "MFDS-CLOP-EVIDENCE", "클로피도그렐근거정", "제약", "Clopidogrel Bisulfate", None,
                    "P-CLOP", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-CLOP", "클로피도그렐정", "제약", "Clopidogrel Bisulfate", None,
                    None, "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-CLOP")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["clopidogrel"])
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")


    def test_manually_reviewed_combo_active_moiety_alias_is_materialized(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Alogliptin",), ("Metformin",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-ALO-COMBO", "알로글립틴복합정", "ING-ALO-COMBO",
                "alogliptin benzoate (as alogliptin)+metformin hydrochloride",
            ),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-ALO-COMBO", "알로글립틴복합정", "제약",
                "Alogliptin Benzoate/Metformin Hydrochloride", None,
                "P-ALO-COMBO", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-ALO-COMBO")

        self.assertIn("alogliptin benzoate (as alogliptin)", result["aliases"])
        self.assertEqual(product["ingredient_mapping_status"], "partial")
        self.assertIn("alogliptin", product["safety_ingredients"])

    def test_manually_reviewed_salt_alias_requires_current_source_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Alfuzosin",))
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-ALF-NO-EVIDENCE", "알푸조신정", "제약", "Alfuzosin Hydrochloride", None,
                None, "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-ALF-NO-EVIDENCE")

        self.assertNotIn("alfuzosin hydrochloride", result["aliases"])
        self.assertEqual(product["ingredient_mapping_status"], "not_evaluable")

    def test_manually_reviewed_salt_alias_accepts_current_product_dur_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Phendimetrazine",))
        dur.execute(
            """INSERT INTO product_dur(
                dataset_key,source_row,ingredient_name,ingredient_code,product_code,
                paired_ingredient_name,paired_ingredient_code,paired_product_code
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "product:age_contraindication", 1, "Phendimetrazine Tartrate",
                "552601ATB", "P-PHEND", None, None, None,
            ),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-PHEND", "펜디메트라진정", "제약", "Phendimetrazine Tartrate", None,
                None, "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-PHEND")

        self.assertIn("phendimetrazine tartrate", result["aliases"])
        self.assertEqual(
            result["aliases"]["phendimetrazine tartrate"]["target"],
            "phendimetrazine",
        )
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["phendimetrazine"])

    def test_manually_reviewed_alias_accepts_current_dur_product_catalog_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Gemcitabine",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-GEM", "젬시타빈주", "ING-GEM",
                "Gemcitabine Hydrochloride (as Gemcitabine 2g(38mg/mL))",
            ),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-GEM", "젬시타빈주", "제약",
                "Gemcitabine Hydrochloride", None, None,
                "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)

        self.assertIn(
            "gemcitabine hydrochloride (as gemcitabine 2g(38mg/ml))",
            result["aliases"],
        )
        self.assertEqual(
            result["aliases"][
                "gemcitabine hydrochloride (as gemcitabine 2g(38mg/ml))"
            ]["target"],
            "gemcitabine",
        )


    def test_manually_reviewed_salt_alias_uses_exact_edi_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Alfuzosin",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-ALF", "알푸조신정", "ING-ALF", "alfuzosin hydrochloride"),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-ALF", "알푸조신정", "제약", "Alfuzosin Hydrochloride", None,
                "P-ALF", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-ALF")

        self.assertIn("alfuzosin hydrochloride", result["aliases"])
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["alfuzosin"])

    def test_manually_reviewed_nomenclature_variant_is_materialized(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Raloxifen",))
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-RAL", "라록시펜캡슐", "ING-RAL", "raloxifene"),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-RAL", "라록시펜캡슐", "제약", "Raloxifene Hydrochloride", None,
                "P-RAL", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-RAL")

        self.assertIn("raloxifene", result["aliases"])
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["raloxifen"])


    def test_exact_edi_combo_can_resolve_single_remaining_component(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Ezetimibe",), ("Simvastatin",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-EZE-COMBO", "에제심바정", "ING-EZE-COMBO", "ezetimib,simvastatin"),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.executemany(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "MFDS-EZE-EVIDENCE", "에제심바정", "제약", "Ezetimibe/Simvastatin", None,
                    "P-EZE-COMBO", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-EZE-TARGET", "에제티미브정", "제약", "Ezetimibe", None,
                    None, "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-EZE-EVIDENCE")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["ezetimibe", "simvastatin"])
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")

    def test_malformed_combo_as_annotation_cannot_cross_map_ingredients(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Dapagliflozin",), ("Metformin",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-BAD-COMBO", "잘못표기복합정", "ING-BAD-COMBO",
                "dapagliflozin propanediol hydrate (as dapagliflozin)+metformin hydrochloride (as dapagliflozin)",
            ),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.executemany(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    "MFDS-BAD-COMBO", "잘못표기복합정", "제약",
                    "Dapagliflozin Propanediol Hydrate/Metformin Hydrochloride", None,
                    "P-BAD-COMBO", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-MET-TARGET", "메트포르민정", "제약", "Metformin Hydrochloride", None,
                    None, "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-MET-TARGET")

        self.assertEqual(product["ingredient_mapping_status"], "not_evaluable")
        self.assertEqual(product["safety_ingredients"], [])



if __name__ == "__main__":
    unittest.main()
