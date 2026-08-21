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
            add_product(
                con,
                "VITAMIN-D3",
                "비타민D3비오엔주(콜레칼시페롤)",
                "Cholecalciferol",
            )
            add_product(
                con,
                "GRAM-ASCII",
                "셉타신주1g(세프메타졸나트륨)",
                "Cefmetazole Sodium",
            )
            add_product(
                con,
                "GRAM-KOREAN",
                "대웅세포티암염산염주1그램",
                "Cefotiam Hydrochloride",
            )
            add_product(
                con,
                "IU-ASCII",
                "에스포젠프리필드주4000IU",
                "Epoetin Alfa",
            )
            add_product(
                con,
                "IU-KOREAN",
                "옥시톤주5아이유",
                "Oxytocin",
            )
            add_product(
                con,
                "IU-UNIT-KOREAN",
                "그린진에프주500단위",
                "Coagulation Factor VIII",
            )
            add_product(
                con,
                "PERCENT-TARGET",
                "푸카인0.5%주사",
                "Bupivacaine Hydrochloride",
            )
            add_product(
                con,
                "PERCENT-MASS-DISTRACTOR",
                "인데놀정10mg",
                "Propranolol Hydrochloride",
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

    def test_mixed_script_code_like_name_matches_across_spacing_boundary(self) -> None:
        compact = parse_product_search_query("비타민D3")
        spaced = parse_product_search_query("비타민 D3")
        explicit_strength = parse_product_search_query("비타민E400IU")

        self.assertEqual(compact.number_tokens, ())
        self.assertEqual(spaced.number_tokens, ())
        self.assertEqual(explicit_strength.text_tokens, ("비타민e",))
        self.assertEqual(explicit_strength.strength_atoms, (("400", "iu"),))
        self.assertEqual(
            self.repo.search("비타민 D3", limit=10)[0]["product_ref"],
            "VITAMIN-D3",
        )

    def test_real_catalog_strength_unit_aliases_share_canonical_units(self) -> None:
        cases = (
            ("셉타신 1그램", "GRAM-ASCII"),
            ("대웅세포티암 1g", "GRAM-KOREAN"),
            ("에스포젠 4000 단위", "IU-ASCII"),
            ("옥시톤 5 IU", "IU-KOREAN"),
            ("그린진 500 IU", "IU-UNIT-KOREAN"),
        )
        for query_text, expected in cases:
            with self.subTest(query=query_text):
                results = self.repo.search(query_text, limit=10)
                self.assertTrue(results)
                self.assertEqual(results[0]["product_ref"], expected)

        self.assertEqual(parse_product_search_query("셉타신 1그램").unit_tokens, ("g",))
        self.assertEqual(parse_product_search_query("옥시톤 5아이유").unit_tokens, ("iu",))
        self.assertEqual(parse_product_search_query("그린진 500단위").unit_tokens, ("iu",))

    def test_percent_is_an_explicit_strength_unit_not_a_unitless_number(self) -> None:
        percent = parse_product_search_query("푸카인 0.5%")
        self.assertEqual(percent.unit_tokens, ("pct",))
        self.assertEqual(percent.strength_atoms, (("0.5", "pct"),))
        self.assertEqual(
            self.repo.search("푸카인 0.5%", limit=10)[0]["product_ref"],
            "PERCENT-TARGET",
        )
        self.assertEqual(self.repo.search("인데놀 10%", limit=10), [])

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