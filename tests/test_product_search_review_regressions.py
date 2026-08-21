from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.product_search import match_product_fields, parse_product_search_query
from medicine_app.products import ProductRepository
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db


class ProductSearchReviewRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.canonical_db = Path(self.tmp.name) / "canonical.sqlite"
        make_canonical_db(self.canonical_db)
        with sqlite3.connect(self.canonical_db) as con:
            add_product(
                con,
                "OCR-TARGET",
                "씬지록신정25마이크로그램(레보티록신나트륨수화물)",
                "Levothyroxine Sodium Hydrate",
            )
            add_product(
                con,
                "OCR-DISTRACTOR",
                "무관정25밀리그램",
                "씬지록심 25",
            )
            add_product(
                con,
                "OCR-INACTIVE-NAME",
                "씬지록심정25밀리그램",
                "Inactive Distractor",
                permit_status="inactive",
                cancel_name="취소",
            )
            add_product(
                con,
                "LEADING-DOT",
                "알프람정0.25밀리그램(알프라졸람)",
                "Alprazolam",
            )
            add_product(
                con,
                "RAW-LEADING-DOT",
                "Lead.25mg정",
                "Synthetic Ingredient",
            )
            add_product(
                con,
                "OCR-TAJIN",
                "타진서방정 10/5mg",
                "Oxycodone/Naloxone",
            )
            add_product(
                con,
                "OCR-TYLENOL",
                "Tylenol 500mg Tablet",
                "Acetaminophen",
            )
            add_product(
                con,
                "OCR-GABAPENTIN",
                "Gabapentin 100mg Capsule",
                "Gabapentin",
            )
            add_product(
                con,
                "OCR-RESCUE-TARGET",
                "yabc정5mg",
                "Synthetic Ingredient",
            )
            add_product(
                con,
                "OCR-RESCUE-DISTRACTOR",
                "xabxc정5mg",
                "Synthetic Ingredient",
            )
            add_product(
                con,
                "KOREAN-MILLI",
                "알로판400밀리그램정",
                "Ibuprofen",
            )
            add_product(
                con,
                "GREEK-BETA-TARGET",
                "뉴베타주2mL(디클로페낙β-디메틸아미노에탄올)",
                "Diclofenac Beta-Dimethylaminoethanol",
            )
            add_product(
                con,
                "GREEK-BETA-DISTRACTOR",
                "로페낙주2mL(디클로페낙나트륨)",
                "Diclofenac Sodium",
            )
            add_product(
                con,
                "VITAMIN-B12",
                "Vitamin B12 Tablet",
                "Cyanocobalamin",
            )
            add_product(
                con,
                "B12-DISTRACTOR",
                "Influenza Vaccine",
                "Influenza B/California/12/2015",
            )
        self.repo = ProductRepository(self.canonical_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ocr_fuzzy_product_name_competes_with_exact_other_field_matches(self) -> None:
        results = self.repo.search("씬지록심 25", limit=10, mode="ocr", explain=True)

        self.assertEqual(results[0]["product_ref"], "OCR-TARGET")
        self.assertEqual(results[0]["search_match"]["field"], "product_name")
        self.assertEqual(results[0]["search_match"]["tier"], "ocr_fuzzy")
        self.assertIn("OCR-DISTRACTOR", [row["product_ref"] for row in results])

        with_inactive = self.repo.search(
            "씬지록심 25",
            limit=10,
            mode="ocr",
            explain=True,
            include_inactive=True,
        )
        self.assertEqual(with_inactive[0]["product_ref"], "OCR-TARGET")

    def test_ocr_fuzzy_fragment_rescue_competes_with_deterministic_fuzzy_products(self) -> None:
        query = parse_product_search_query("xabc 5mg", mode="ocr")
        self.assertIsNotNone(match_product_fields(query, product_name="yabc정5mg"))
        self.assertIsNotNone(match_product_fields(query, product_name="xabxc정5mg"))

        results = self.repo.search("xabc 5mg", limit=10, mode="ocr", explain=True)

        self.assertEqual(results[0]["product_ref"], "OCR-RESCUE-TARGET")
        self.assertEqual(results[0]["search_match"]["tier"], "ocr_fuzzy")
        self.assertIn("OCR-RESCUE-DISTRACTOR", [row["product_ref"] for row in results])

    def test_leading_dot_decimal_is_equivalent_to_zero_prefixed_decimal(self) -> None:
        shorthand = parse_product_search_query("알프람 .25")
        explicit = parse_product_search_query("알프람 0.25")

        self.assertEqual(shorthand.number_tokens, ("0.25",))
        self.assertEqual(shorthand.strength_atoms, explicit.strength_atoms)
        self.assertEqual(
            self.repo.search("알프람 .25", limit=10)[0]["product_ref"],
            "LEADING-DOT",
        )
        self.assertEqual(
            self.repo.search("Lead 0.25 mg", limit=10)[0]["product_ref"],
            "RAW-LEADING-DOT",
        )

    def test_korean_strength_units_allow_one_internal_spacing_boundary(self) -> None:
        micro = parse_product_search_query("씬지록신 25 마이크로 그램")
        micro_variant = parse_product_search_query("씬지록신 25 마이크로 그람")
        milli = parse_product_search_query("알로판 400 밀리 그램")

        self.assertEqual(micro.unit_tokens, ("ug",))
        self.assertEqual(micro_variant.unit_tokens, ("ug",))
        self.assertEqual(milli.unit_tokens, ("mg",))
        self.assertEqual(
            self.repo.search("씬지록신 25 마이크로 그램", limit=10)[0]["product_ref"],
            "OCR-TARGET",
        )
        self.assertEqual(
            self.repo.search("씬지록신 25 마이크로 그람", limit=10)[0]["product_ref"],
            "OCR-TARGET",
        )
        self.assertEqual(
            self.repo.search("알로판 400 밀리 그램", limit=10)[0]["product_ref"],
            "KOREAN-MILLI",
        )

    def test_non_ascii_letter_qualifiers_are_not_silently_dropped(self) -> None:
        query = parse_product_search_query("디클로페낙β 2 ml")

        self.assertIn("β", "".join(query.text_tokens))
        results = self.repo.search("디클로페낙β 2 ml", limit=10)
        self.assertTrue(results)
        self.assertEqual(results[0]["product_ref"], "GREEK-BETA-TARGET")
        self.assertNotIn(
            "GREEK-BETA-DISTRACTOR",
            [row["product_ref"] for row in results],
        )

    def test_adjacent_latin_name_digits_remain_lexical_without_a_strength_unit(self) -> None:
        compact = parse_product_search_query("B12")
        prefixed = parse_product_search_query("Vitamin B12")

        self.assertEqual(compact.text_tokens, ("b12",))
        self.assertEqual(compact.number_tokens, ())
        self.assertEqual(prefixed.text_tokens, ("vitamin", "b12"))
        self.assertEqual(prefixed.number_tokens, ())
        results = self.repo.search("Vitamin B12", limit=10)
        self.assertEqual(results[0]["product_ref"], "VITAMIN-B12")
        self.assertNotIn("B12-DISTRACTOR", [row["product_ref"] for row in results])

    def test_long_latin_brand_with_adjacent_digits_still_means_strength(self) -> None:
        query = parse_product_search_query("Tylenol500")

        self.assertEqual(query.text_tokens, ("tylenol",))
        self.assertEqual(query.number_tokens, ("500",))
        self.assertEqual(
            self.repo.search("Tylenol500", limit=10)[0]["product_ref"],
            "OCR-TYLENOL",
        )

    def test_very_long_integer_search_does_not_depend_on_decimal_context(self) -> None:
        digits = "9" * 5000

        query = parse_product_search_query(f"nevermatch {digits}")

        self.assertEqual(query.number_tokens, (digits,))
        self.assertEqual(self.repo.search(f"nevermatch {digits}", limit=10), [])
        self.assertEqual(self.repo.search(digits, limit=10), [])

    def test_ocr_trailing_dose_uses_intake_fraction_and_form_grammar(self) -> None:
        cases = (
            ("타진서방정 10/5mg1/2정", "OCR-TAJIN"),
            ("타진서방정 10/5mg 1/2정", "OCR-TAJIN"),
            ("타진서방정 10/5mg½정", "OCR-TAJIN"),
            ("Tylenol 500mg1 tablet", "OCR-TYLENOL"),
            ("Tylenol 500mg 1 tablet", "OCR-TYLENOL"),
            ("Gabapentin 100mg1 capsule", "OCR-GABAPENTIN"),
        )
        for query_text, expected in cases:
            with self.subTest(query=query_text):
                results = self.repo.search(query_text, limit=10, mode="ocr")
                self.assertTrue(results)
                self.assertEqual(results[0]["product_ref"], expected)


if __name__ == "__main__":
    unittest.main()