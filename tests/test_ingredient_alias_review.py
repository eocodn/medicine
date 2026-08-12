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
from tests.test_product_mapping import make_catalog_db, make_dur_db


class IngredientAliasReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.dur_db = root / "dur.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reviewed_multi_identity_alias_preserves_complementary_menotrophin_rules(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Menotrophin",), ("Menotrophin HP",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-MENO-HP", "메노트로핀에이치피주", "ING-MENO", "Menotrophin"),
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
                "MFDS-MENO-HP", "메노트로핀에이치피주", "제약", "Menotrophin HP", None,
                "P-MENO-HP", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-MENO-HP")

        self.assertEqual(
            result["multi_aliases"]["menotrophin hp"]["targets"],
            ["menotrophin", "menotrophin hp"],
        )
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["menotrophin", "menotrophin hp"])

    def test_reviewed_multi_identity_alias_requires_current_source_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Menotrophin",), ("Menotrophin HP",)],
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
                "MFDS-MENO-HP-NO-EDI", "메노트로핀에이치피주", "제약", "Menotrophin HP", None,
                None, "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-MENO-HP-NO-EDI")

        self.assertNotIn("menotrophin hp", result.get("multi_aliases", {}))
        self.assertEqual(product["safety_ingredients"], ["menotrophin hp"])

    def test_reviewed_multi_identity_alias_accepts_current_dur_product_catalog_observation(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Cyclosporin",), ("Cyclosporine",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-CYC-MULTI", "사이클로스포린액", "ING-CYC",
                "Microemulsion Cyclosporine 5g(0.1g/mL)",
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
                "MFDS-CYC-MULTI", "사이클로스포린액", "제약",
                "Microemulsion Cyclosporine 5g(0.1g/mL)", None, None,
                "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-CYC-MULTI")

        self.assertEqual(
            result["multi_aliases"]["microemulsion cyclosporine 5g(0.1g/ml)"]["targets"],
            ["cyclosporin", "cyclosporine"],
        )
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["cyclosporin", "cyclosporine"])

    def test_reviewed_multi_identity_alias_maps_azilsartan_form_to_both_rule_identities(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Azilsartan Medoxomil",), ("Azilsartan Medoxomil Potassium",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            (
                "P-AZI", "이달비정", "ING-AZI",
                "Azilsartan Medoxomil Potassium (as Azilsartan Medoxomil)",
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
                "MFDS-AZI", "이달비정", "제약", "Potassium Azilsartan Medoxomil", None,
                "P-AZI", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        result = materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-AZI")

        self.assertEqual(
            result["multi_aliases"]["potassium azilsartan medoxomil"]["targets"],
            ["azilsartan medoxomil", "azilsartan medoxomil potassium"],
        )
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(
            product["safety_ingredients"],
            ["azilsartan medoxomil", "azilsartan medoxomil potassium"],
        )

    def test_conflicting_known_exact_edi_ingredients_fail_closed(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Somatropin",), ("Somatostatin",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("668903311", "유트로핀에스펜주", "ING-SOM", "Somatostatin"),
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
                "MFDS-SOM-CONFLICT", "유트로핀에스펜주", "제약", "Somatropin", None,
                "668903311", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-SOM-CONFLICT")

        self.assertEqual(product["product_mapping_status"], "matched")
        self.assertEqual(product["ingredient_mapping_status"], "not_evaluable")
        self.assertEqual(product["ingredient_mapping_method"], "conflicting_exact_edi_identity")
        self.assertEqual(product["safety_ingredients"], [])
        self.assertIn("서로 다른", product["ingredient_mapping_reason"] or "")

    def test_unreviewed_known_exact_edi_name_difference_is_not_globally_blocked(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Olmesartan Medoxomil",), ("Olmesartan",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-OLM", "올메사르탄정", "ING-OLM", "Olmesartan"),
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
                "MFDS-OLM", "올메사르탄정", "제약", "Olmesartan Medoxomil", None,
                "P-OLM", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-OLM")

        self.assertEqual(product["product_mapping_status"], "matched")
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["olmesartan"])

    def test_known_exact_edi_conflict_does_not_contaminate_valid_alias_graph(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Somatropin",), ("Somatostatin",)],
        )
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("668903311", "유트로핀에스펜주", "ING-SOM-R", "Somatostatin"),
                (
                    "P-SST", "소마토린주", "ING-SST",
                    "Somatostatin Acetate (as Somatostatin)",
                ),
            ],
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
                    "MFDS-SOM-CONFLICT", "유트로핀에스펜주", "제약", "Somatropin", None,
                    "668903311", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-SST", "소마토린주", "제약", "Somatostatin Acetate", None,
                    "P-SST", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        with sqlite3.connect(self.dur_db) as dur, sqlite3.connect(self.catalog_db) as catalog:
            report = derive_validated_ingredient_aliases(dur, catalog)

        self.assertEqual(
            report["aliases"]["somatostatin acetate (as somatostatin)"]["target"],
            "somatostatin",
        )
        self.assertNotIn("somatostatin acetate (as somatostatin)", report["ambiguous"])

    def test_same_dur_ingredient_code_validates_name_variants(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Tramadol",))
        dur.executemany(
            """INSERT INTO product_dur(
                dataset_key,source_row,ingredient_name,ingredient_code,product_code,
                paired_ingredient_name,paired_ingredient_code,paired_product_code
            ) VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("product:dose_caution", 1, "Tramadol", "ING-TRAM", "P-TRAM", None, None, None),
                ("product:age_contraindication", 2, "Tramadol Hydrochloride", "ING-TRAM", "P-TRAM", None, None, None),
            ],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-TRAM", "트라마돌근거정", "ING-TRAM", "Tramadol Hydrochloride"),
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
                "MFDS-TRAM", "트라마돌정", "제약", "Tramadol Hydrochloride", None,
                "P-TRAM", "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-TRAM")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["tramadol"])
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")

    def test_combination_product_code_cannot_prove_cross_ingredient_alias(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Amlodipine",), ("Metformin",)],
        )
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-COMBO-CODE", "복합근거정", "ING-COMBO", "amlodipine+metformin"),
        )
        dur.executemany(
            """INSERT INTO product_dur(
                dataset_key,source_row,ingredient_name,ingredient_code,product_code,
                paired_ingredient_name,paired_ingredient_code,paired_product_code
            ) VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("product:dose_caution", 1, "Amlodipine", "ING-COMBO", "P-COMBO-CODE", None, None, None),
                ("product:age_contraindication", 2, "Metformin Hydrochloride", "ING-COMBO", "P-COMBO-CODE", None, None, None),
            ],
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
                "MFDS-MET-CODE", "메트포르민정", "제약", "Metformin Hydrochloride", None,
                None, "2026-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-MET-CODE")

        self.assertEqual(product["ingredient_mapping_status"], "not_evaluable")
        self.assertEqual(product["safety_ingredients"], [])

    def test_transitive_edi_evidence_recovers_product_side_hcl_variant(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute("INSERT INTO ingredient_dur(ingredient_name) VALUES(?)", ("Amitriptyline",))
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("P-AMIT-BASE", "아미트립틸린근거정", "ING-A1", "amitriptyline"),
                ("P-AMIT-HCL", "아미트립틸린HCL정", "ING-A2", "amitriptyline HCl"),
            ],
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
                    "MFDS-AMIT-BASE", "아미트립틸린근거정", "제약", "Amitriptyline Hydrochloride", None,
                    "P-AMIT-BASE", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-AMIT-HCL", "아미트립틸린HCL정", "제약", "Amitriptyline Hydrochloride", None,
                    "P-AMIT-HCL", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-AMIT-HCL")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["amitriptyline"])
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")

    def test_validated_aliases_apply_to_comma_delimited_product_ingredients(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Acetaminophen",), ("Tramadol",)],
        )
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("P-TRA-BASE", "트라마돌근거정", "ING-T1", "tramadol"),
                ("P-COMBO", "아세트라마정", "ING-C1", "acetaminophen,tramadol hydrochloride"),
            ],
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
                    "MFDS-TRA-BASE", "트라마돌근거정", "제약", "Tramadol Hydrochloride", None,
                    "P-TRA-BASE", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-COMBO", "아세트라마정", "제약", "Acetaminophen/Tramadol Hydrochloride", None,
                    "P-COMBO", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        materialize_validated_ingredient_aliases(self.dur_db, self.catalog_db)
        product = ProductRepository(self.dur_db, self.catalog_db).get("MFDS-COMBO")

        self.assertEqual(product["ingredient_mapping_status"], "matched")
        self.assertEqual(product["safety_ingredients"], ["acetaminophen", "tramadol"])
        self.assertEqual(product["ingredient_mapping_method"], "validated_alias")

    def test_conflicting_sibling_evidence_is_not_materialized(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO ingredient_dur(ingredient_name) VALUES(?)",
            [("Morphine",), ("Morphine Sulfate",)],
        )
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("P-MOR-1", "모르핀근거1", "ING-M1", "morphine"),
                ("P-MOR-2", "모르핀근거2", "ING-M2", "morphine sulfate"),
            ],
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
                    "MFDS-MOR-1", "모르핀근거1", "제약", "Morphine Sulfate Hydrate", None,
                    "P-MOR-1", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
                (
                    "MFDS-MOR-2", "모르핀근거2", "제약", "Morphine Sulfate Hydrate", None,
                    "P-MOR-2", "2026-01-01", None, "정상", "active", "fixture", "{}",
                ),
            ],
        )
        catalog.commit()
        catalog.close()

        with sqlite3.connect(self.dur_db) as dur, sqlite3.connect(self.catalog_db) as catalog:
            report = derive_validated_ingredient_aliases(dur, catalog)

        self.assertNotIn("morphine sulfate hydrate", report["aliases"])
        self.assertEqual(
            report["ambiguous"]["morphine sulfate hydrate"]["targets"],
            ["morphine", "morphine sulfate"],
        )



if __name__ == "__main__":
    unittest.main()
