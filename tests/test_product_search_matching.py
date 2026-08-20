from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import MedicationApp
from medicine_app.product_search import match_product_fields, parse_product_search_query
from medicine_canonical.mobile import build_mobile_database
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db
from tests.test_safety_coverage import make_canonical_db as make_release_canonical_db


class ProductSearchQueryTest(unittest.TestCase):
    def test_normalizes_spacing_boundaries_and_strength_units(self) -> None:
        spaced = parse_product_search_query(" 씬지록신 25 ㎍ ")
        compact = parse_product_search_query("씬지록신25mcg")

        self.assertEqual(spaced.text_tokens, ("씬지록신",))
        self.assertEqual(spaced.number_tokens, ("25",))
        self.assertEqual(spaced.unit_tokens, ("ug",))
        self.assertEqual(compact.text_tokens, spaced.text_tokens)
        self.assertEqual(compact.number_tokens, spaced.number_tokens)
        self.assertEqual(compact.unit_tokens, spaced.unit_tokens)

    def test_strength_numbers_are_exact_tokens_not_substrings(self) -> None:
        query = parse_product_search_query("씬지록신 25")

        exact = match_product_fields(
            query,
            product_name="씬지록신정25마이크로그램(레보티록신나트륨수화물)",
        )
        collision = match_product_fields(
            query,
            product_name="씬지록신정125마이크로그램(레보티록신나트륨수화물)",
        )

        self.assertIsNotNone(exact)
        self.assertIsNone(collision)

    def test_only_code_shaped_queries_use_identifier_candidate_lookup(self) -> None:
        self.assertTrue(parse_product_search_query("EDI-SYN-25").identifier_like)
        self.assertTrue(parse_product_search_query("SYN-25").identifier_like)
        self.assertFalse(parse_product_search_query("씬지록신 25").identifier_like)
        self.assertFalse(parse_product_search_query("Tylenol 500").identifier_like)

    def test_ocr_mode_tolerates_only_bounded_text_error_and_trailing_regimen(self) -> None:
        contaminated = parse_product_search_query("타진서방정 10/5mg0.5정", mode="ocr")
        typo = parse_product_search_query("씬지록심 25", mode="ocr")
        manual_typo = parse_product_search_query("씬지록심 25", mode="manual")

        self.assertEqual(contaminated.number_tokens, ("10", "5"))
        self.assertEqual(contaminated.unit_tokens, ("mg",))
        self.assertIsNotNone(match_product_fields(
            contaminated,
            product_name="타진서방정 10/5mg",
        ))
        self.assertIsNotNone(match_product_fields(
            typo,
            product_name="씬지록신정25마이크로그램(레보티록신나트륨수화물)",
        ))
        self.assertIsNone(match_product_fields(
            manual_typo,
            product_name="씬지록신정25마이크로그램(레보티록신나트륨수화물)",
        ))


class ProductSearchIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        with sqlite3.connect(self.canonical_db) as con:
            add_product(
                con,
                "SYN-25",
                "씬지록신정25마이크로그램(레보티록신나트륨수화물)",
                "Levothyroxine Sodium Hydrate",
                manufacturer="부광약품",
                edi="EDI-SYN-25",
            )
            add_product(
                con,
                "SYN-125",
                "씬지록신정125마이크로그램(레보티록신나트륨수화물)",
                "Levothyroxine Sodium Hydrate",
                manufacturer="부광약품",
            )
            add_product(
                con,
                "GABA-25",
                "가바뉴로캡슐25밀리그램(프레가발린)",
                "Pregabalin",
            )
            add_product(
                con,
                "TAJIN-10-5",
                "타진서방정 10/5mg",
                "Oxycodone/Naloxone",
            )
            add_product(
                con,
                "ALPRAM-025",
                "알프람정0.25밀리그램(알프라졸람)",
                "Alprazolam",
            )
            add_product(
                con,
                "DIGEST-500",
                "베스타제정",
                "Biodiastase 500/Cellulase AP3/Lipase AP6",
            )
            add_product(
                con,
                "HARTMANN",
                "하트만용액",
                "Calcium Chloride Hydrate/Potassium Chloride/Sodium Chloride/Sodium Lactate Solution 50%",
            )
        self.app = MedicationApp(self.canonical_db, self.personal_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assert_first(self, query: str, product_ref: str, *, mode: str = "manual") -> None:
        results = self.app.search_products(query, limit=10, mode=mode)
        self.assertTrue(results, query)
        self.assertEqual(results[0]["product_ref"], product_ref, query)

    def test_structured_product_name_search_matches_omitted_form_and_spacing(self) -> None:
        self.assert_first("씬지록신 25", "SYN-25")
        self.assert_first("씬지록신25", "SYN-25")
        self.assert_first("씬지록신 25 mcg", "SYN-25")
        self.assert_first("가바뉴로 25", "GABA-25")
        self.assert_first("타진 10 5", "TAJIN-10-5")
        self.assert_first("알프람 0.25", "ALPRAM-025")

    def test_structured_strength_search_rejects_numeric_collisions(self) -> None:
        results = self.app.search_products("씬지록신 25", limit=10)

        self.assertEqual([row["product_ref"] for row in results], ["SYN-25"])

    def test_structured_search_preserves_numeric_ingredient_matches(self) -> None:
        self.assert_first("Biodiastase 500", "DIGEST-500")

        results = self.app.search_products("Sodium Lactate Solution 50%", limit=10)
        self.assertIn("HARTMANN", [row["product_ref"] for row in results])

    def test_punctuation_separated_text_uses_normalized_matching(self) -> None:
        self.assert_first("하트만-용액", "HARTMANN")

    def test_structured_candidate_generation_does_not_truncate_valid_brand_form_query(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            source_key = con.execute(
                "SELECT source_dataset_key FROM products ORDER BY item_seq LIMIT 1"
            ).fetchone()[0]
            start_row = con.execute("SELECT MAX(source_row) FROM products").fetchone()[0] + 1
            products = [
                (
                    f"DISTRACTOR-{index:04d}",
                    start_row + index,
                    f"가짜{index:04d}정10밀리그램",
                    "제약",
                    "Distractor",
                    "정제",
                    "2020-01-01",
                    None,
                    "정상",
                    "active",
                    source_key,
                )
                for index in range(1100)
            ]
            con.executemany(
                """INSERT INTO products(
                       item_seq,source_row,product_name,manufacturer,ingredient_text,dosage_form,
                       permit_date,cancel_date,cancel_name,permit_status,source_dataset_key
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                products,
            )
            con.executemany(
                "INSERT INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
                [
                    (item_seq, "MFDS_ITEM_SEQ", item_seq, source_key)
                    for item_seq, *_rest in products
                ],
            )
            add_product(
                con,
                "LIPITOR-10",
                "리피토정10밀리그램(아토르바스타틴칼슘삼수화물)",
                "Atorvastatin Calcium Trihydrate",
            )

        self.assert_first("리피토 정 10", "LIPITOR-10")

    def test_structured_identifier_queries_preserve_item_seq_and_edi_search(self) -> None:
        self.assert_first("SYN-25", "SYN-25")
        self.assert_first("EDI-SYN-25", "SYN-25")

    def test_ocr_mode_recovers_contamination_and_one_edit_without_auto_identity(self) -> None:
        self.assertEqual(self.app.search_products("타진서방정 10/5mg0.5정", limit=10), [])
        self.assert_first("타진서방정 10/5mg0.5정", "TAJIN-10-5", mode="ocr")
        self.assertEqual(self.app.search_products("씬지록심 25", limit=10), [])
        self.assert_first("씬지록심 25", "SYN-25", mode="ocr")
        self.assert_first("씬즈록신 25", "SYN-25", mode="ocr")

        result = self.app.search_products("씬지록심 25", limit=10, mode="ocr")[0]
        self.assertEqual(result["product_mapping_method"], "item_seq_exact")
        self.assertNotIn("search_match", result)

    def test_structured_search_requires_no_search_specific_database_object(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            search_objects = con.execute(
                "SELECT name FROM sqlite_master WHERE lower(name) LIKE '%search%'"
            ).fetchall()
        self.assertEqual(search_objects, [])
        self.assert_first("씬지록신 25", "SYN-25")

    def test_current_contract_v1_mobile_database_needs_no_search_specific_object(self) -> None:
        root = Path(self.tmp.name)
        canonical = root / "release-canonical.sqlite"
        mobile = root / "release-mobile.sqlite"
        personal = root / "release-personal.sqlite"
        make_release_canonical_db(canonical)
        with sqlite3.connect(canonical) as con:
            add_product(
                con,
                "CONTRACT-25",
                "씬지록신정25마이크로그램(레보티록신나트륨수화물)",
                "Levothyroxine Sodium Hydrate",
            )
        result = build_mobile_database(canonical, mobile)

        with sqlite3.connect(mobile) as con:
            search_objects = con.execute(
                "SELECT name FROM sqlite_master WHERE lower(name) LIKE '%search%'"
            ).fetchall()
        app = MedicationApp(mobile, personal)
        matches = app.search_products("씬지록신 25", limit=10)

        self.assertEqual(result["contract_major"], 1)
        self.assertEqual(search_objects, [])
        self.assertEqual(matches[0]["product_ref"], "CONTRACT-25")


if __name__ == "__main__":
    unittest.main()