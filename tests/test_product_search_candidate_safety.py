from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.product_search import match_product_fields, parse_product_search_query
from medicine_app.products import ProductRepository
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db


class ProductSearchCandidateSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.canonical_db = Path(self.tmp.name) / "canonical.sqlite"
        make_canonical_db(self.canonical_db)
        with sqlite3.connect(self.canonical_db) as con:
            add_product(
                con,
                "COMPAT-MIXED-ROMAN",
                "xⅱⅡ정5mg",
                "Synthetic Ingredient",
            )
            add_product(
                con,
                "COMPAT-HANGUL-SYMBOL",
                "기호제조정",
                "Synthetic Ingredient",
                manufacturer="㈜테스트팜",
            )
            add_product(
                con,
                "OCR-COMPAT-FRAGMENT",
                "Ⅻabx정5mg",
                "Synthetic Ingredient",
            )
        self.repo = ProductRepository(self.canonical_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assert_first(self, query: str, product_ref: str) -> None:
        results = self.repo.search(query, limit=10)
        self.assertTrue(results, query)
        self.assertEqual(results[0]["product_ref"], product_ref, query)

    def test_text_candidates_do_not_require_raw_roman_numeral_spelling(self) -> None:
        query_text = "xiiii 5mg"
        raw_name = "xⅱⅡ정5mg"
        query = parse_product_search_query(query_text)

        self.assertIsNotNone(match_product_fields(query, product_name=raw_name))
        self.assert_first(query_text, "COMPAT-MIXED-ROMAN")

    def test_text_candidates_cover_symbol_to_hangul_compatibility_forms(self) -> None:
        query = parse_product_search_query("(주) 테스트팜")

        self.assertIsNotNone(match_product_fields(
            query,
            product_name="",
            manufacturer="㈜테스트팜",
        ))
        self.assert_first("(주) 테스트팜", "COMPAT-HANGUL-SYMBOL")

    def test_ocr_similarity_recovers_compatibility_form_with_one_edit(self) -> None:
        query = parse_product_search_query("xiiabc 5mg", mode="ocr")

        self.assertIsNotNone(match_product_fields(query, product_name="Ⅻabx정5mg"))
        self.assertEqual(self.repo.search("xiiabc 5mg", limit=10), [])
        results = self.repo.search("xiiabc 5mg", limit=10, mode="ocr")
        self.assertEqual(results[0]["product_ref"], "OCR-COMPAT-FRAGMENT")

    def test_many_search_characters_do_not_depend_on_sql_expression_depth(self) -> None:
        def alpha_token(index: int) -> str:
            chars = []
            value = index
            while True:
                value, remainder = divmod(value, 26)
                chars.append(chr(ord("a") + remainder))
                if value == 0:
                    return "token" + "".join(reversed(chars))
                value -= 1

        text_heavy = " ".join(alpha_token(index) for index in range(1200)) + " 5"
        number_heavy = "nevermatch " + " ".join(str(10000 + index) for index in range(1200))

        self.assertEqual(self.repo.search(text_heavy, limit=10), [])
        self.assertEqual(self.repo.search(number_heavy, limit=10), [])


if __name__ == "__main__":
    unittest.main()