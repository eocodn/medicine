from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.product_search import match_product_fields, parse_product_search_query
from medicine_app.products import ProductRepository
from tests.canonical_fixture_support import add_product
from tests.test_safety_coverage import make_canonical_db


class ProductSearchPipelineInvariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "canonical.sqlite"
        make_canonical_db(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def repo(self) -> ProductRepository:
        return ProductRepository(self.db)

    def test_short_queries_use_the_same_canonical_text_as_long_queries(self) -> None:
        with sqlite3.connect(self.db) as con:
            add_product(con, "SHORT-D3", "D3베이스주", "Ingredient")
            add_product(con, "SHORT-II", "II정", "Ingredient")
            con.commit()

        self.assertEqual(self.repo().search("Ｄ３", limit=5)[0]["product_ref"], "SHORT-D3")
        self.assertEqual(self.repo().search("Ⅱ", limit=5)[0]["product_ref"], "SHORT-II")

    def test_candidate_retrieval_does_not_let_lower_fields_hide_product_name_similarity(self) -> None:
        with sqlite3.connect(self.db) as con:
            add_product(con, "PRODUCT-NAME", "AlphaTablet25", "Other Ingredient")
            add_product(con, "INGREDIENT", "Unrelated Product", "Alpha25")
            con.commit()

        result = self.repo().search("Alpha 25", limit=5, explain=True)
        self.assertEqual(result[0]["product_ref"], "PRODUCT-NAME")
        self.assertEqual(result[0]["search_match"]["field"], "product_name")

    def test_explicit_qualifier_sequence_preserves_repeated_strengths(self) -> None:
        query = parse_product_search_query("Combo 10/10mg")

        self.assertEqual(query.explicit_qualifiers, (("10", "mg"), ("10", "mg")))
        self.assertIsNone(match_product_fields(query, product_name="Combo 10mg"))
        self.assertIsNotNone(match_product_fields(query, product_name="Combo 10mg/10mg"))

    def test_exact_identifier_rank_precedes_status_when_inactive_results_are_requested(self) -> None:
        with sqlite3.connect(self.db) as con:
            add_product(con, "ABC", "Inactive Exact", "Ingredient", permit_status="inactive")
            add_product(con, "ABC1", "Active Prefix", "Ingredient")
            con.commit()

        result = self.repo().search("ABC", limit=5, include_inactive=True, explain=True)
        self.assertEqual(result[0]["product_ref"], "ABC")
        self.assertEqual(result[0]["search_match"]["tier"], "identifier_exact")

    def test_complete_exact_field_candidates_are_ranked_before_result_limit(self) -> None:
        with sqlite3.connect(self.db) as con:
            for index in range(260):
                name = "A259" if index == 259 else f"Z{index:03d}"
                add_product(con, f"MFG-{index:03d}", name, "Ingredient", manufacturer="Mega Pharma")
            con.commit()

        result = self.repo().search("Mega Pharma", limit=10, explain=True)
        self.assertEqual(result[0]["product_ref"], "MFG-259")
        self.assertTrue(all(row["search_match"]["field"] == "manufacturer" for row in result))

    def test_explicit_qualifiers_are_sufficient_search_evidence_without_text(self) -> None:
        with sqlite3.connect(self.db) as con:
            add_product(con, "QUALIFIER-ONLY", "Dose987654mg", "Ingredient")
            con.commit()

        result = self.repo().search("987654mg", limit=5, explain=True)
        self.assertEqual(result[0]["product_ref"], "QUALIFIER-ONLY")
        self.assertEqual(result[0]["search_match"]["tier"], "qualifier")

    def test_ocr_regimen_cleanup_is_part_of_the_single_canonical_query(self) -> None:
        with sqlite3.connect(self.db) as con:
            add_product(con, "OCR-REGIMEN", "AB5mg", "Ingredient")
            con.commit()

        result = self.repo().search("AB5mg0.5정", mode="ocr", limit=5, explain=True)
        self.assertEqual(result[0]["product_ref"], "OCR-REGIMEN")


if __name__ == "__main__":
    unittest.main()
