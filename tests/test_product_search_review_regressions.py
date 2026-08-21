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
                "VITAMIN-E400",
                "서흥비타민E400아이유연질캡슐",
                "Tocopherol",
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

    def test_ocr_similarity_product_name_competes_with_exact_other_fields(self) -> None:
        results = self.repo.search("씬지록심 25", limit=10, mode="ocr", explain=True)
        self.assertEqual(results[0]["product_ref"], "OCR-TARGET")
        self.assertEqual(results[0]["search_match"]["field"], "product_name")
        self.assertEqual(results[0]["search_match"]["tier"], "similarity")
        self.assertIn("OCR-DISTRACTOR", [row["product_ref"] for row in results])

        with_inactive = self.repo.search(
            "씬지록심 25", limit=10, mode="ocr", explain=True, include_inactive=True
        )
        self.assertEqual(with_inactive[0]["product_ref"], "OCR-TARGET")

    def test_leading_dot_decimal_matches_zero_prefixed_explicit_unit(self) -> None:
        self.assertEqual(
            self.repo.search("알프람 .25", limit=10)[0]["product_ref"],
            "LEADING-DOT",
        )
        query = parse_product_search_query("Lead 0.25 mg")
        self.assertEqual(query.explicit_qualifiers, (("0.25", "mg"),))
        self.assertEqual(
            self.repo.search("Lead 0.25 mg", limit=10)[0]["product_ref"],
            "RAW-LEADING-DOT",
        )

    def test_korean_and_ascii_unit_aliases_share_explicit_qualifiers(self) -> None:
        cases = (
            ("씬지록신 25 마이크로 그램", "OCR-TARGET", (("25", "ug"),)),
            ("씬지록신 25 마이크로 그람", "OCR-TARGET", (("25", "ug"),)),
            ("알로판 400 밀리 그램", "KOREAN-MILLI", (("400", "mg"),)),
            ("셉타신 1그램", "GRAM-ASCII", (("1", "g"),)),
            ("대웅세포티암 1g", "GRAM-KOREAN", (("1", "g"),)),
            ("에스포젠 4000 단위", "IU-ASCII", (("4000", "iu"),)),
            ("옥시톤 5 IU", "IU-KOREAN", (("5", "iu"),)),
            ("그린진 500 IU", "IU-UNIT-KOREAN", (("500", "iu"),)),
        )
        for query_text, expected, qualifiers in cases:
            with self.subTest(query=query_text):
                self.assertEqual(parse_product_search_query(query_text).explicit_qualifiers, qualifiers)
                results = self.repo.search(query_text, limit=10)
                self.assertTrue(results)
                self.assertEqual(results[0]["product_ref"], expected)

    def test_non_ascii_letters_remain_searchable_characters(self) -> None:
        query = parse_product_search_query("디클로페낙β 2 ml")
        self.assertIn("β", query.normalized)
        results = self.repo.search("디클로페낙β 2 ml", limit=10)
        self.assertEqual(results[0]["product_ref"], "GREEK-BETA-TARGET")
        self.assertNotIn("GREEK-BETA-DISTRACTOR", [row["product_ref"] for row in results])

    def test_alphanumeric_names_keep_bare_digits_as_text(self) -> None:
        self.assertEqual(parse_product_search_query("B12").explicit_qualifiers, ())
        self.assertEqual(parse_product_search_query("Vitamin B12").normalized, "vitaminb12")
        results = self.repo.search("Vitamin B12", limit=10)
        self.assertEqual(results[0]["product_ref"], "VITAMIN-B12")
        self.assertNotIn("B12-DISTRACTOR", [row["product_ref"] for row in results])

        for query_text, expected in (
            ("비타민 D3", "VITAMIN-D3"),
            ("비타민E400", "VITAMIN-E400"),
            ("비타민 E400", "VITAMIN-E400"),
            ("Tylenol500", "OCR-TYLENOL"),
        ):
            with self.subTest(query=query_text):
                self.assertEqual(parse_product_search_query(query_text).explicit_qualifiers, ())
                self.assertEqual(self.repo.search(query_text, limit=10)[0]["product_ref"], expected)

        explicit = parse_product_search_query("비타민E400IU")
        self.assertEqual(explicit.explicit_qualifiers, (("400", "iu"),))

    def test_percent_is_distinct_from_mass_units(self) -> None:
        percent = parse_product_search_query("푸카인 0.5%")
        self.assertEqual(percent.explicit_qualifiers, (("0.5", "pct"),))
        self.assertEqual(self.repo.search("푸카인 0.5%", limit=10)[0]["product_ref"], "PERCENT-TARGET")
        self.assertEqual(self.repo.search("인데놀 10%", limit=10), [])

    def test_very_long_integer_search_is_bounded(self) -> None:
        digits = "9" * 5000
        query = parse_product_search_query(f"nevermatch {digits}")
        self.assertIn(digits, query.normalized)
        self.assertEqual(query.explicit_qualifiers, ())
        self.assertEqual(self.repo.search(f"nevermatch {digits}", limit=10), [])
        self.assertEqual(self.repo.search(digits, limit=10), [])

    def test_ocr_trailing_dose_uses_generic_intake_fraction_and_form_grammar(self) -> None:
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
