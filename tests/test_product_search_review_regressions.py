from __future__ import annotations

import sqlite3
import tempfile
import unittest
import unicodedata
from pathlib import Path

from medicine_app.product_search import match_product_fields, parse_product_search_query
from medicine_app.product_search_numeric import raw_numeric_compat_glob
from medicine_app.products import ProductRepository
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db


COMPAT_TWO_DIGITS = ("𝟚", "𝟐", "𝟤", "𝟮", "𝟸", "🯲")
SCRIPT_TWO_DIGITS = ("٢", "۲", "२", "২", "๒")


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
            for index, raw_digit in enumerate(SCRIPT_TWO_DIGITS):
                add_product(
                    con,
                    f"SCRIPT-DIGIT-{index}",
                    f"Script{raw_digit}mg정",
                    "Synthetic Ingredient",
                )
            for index, raw_digit in enumerate(COMPAT_TWO_DIGITS):
                add_product(
                    con,
                    f"MATH-DIGIT-{index}",
                    f"Math{raw_digit}mg정",
                    "Synthetic Ingredient",
                )
            for index, (raw_letter, normalized_letter) in enumerate(
                (("ẖ", "h"), ("ⱼ", "j"), ("ꟲ", "c"), ("ᶜ", "c"))
            ):
                add_product(
                    con,
                    f"TEXT-COMPAT-{index}",
                    f"x{raw_letter}정5mg",
                    f"Synthetic {normalized_letter}",
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

    def test_mathematical_digits_keep_candidate_sql_a_superset_of_nfkc_matching(self) -> None:
        for index, raw_digit in enumerate(COMPAT_TWO_DIGITS):
            with self.subTest(raw_digit=raw_digit):
                query = parse_product_search_query("Math 2 mg")
                self.assertIsNotNone(
                    match_product_fields(query, product_name=f"Math{raw_digit}mg정")
                )
                results = self.repo.search("Math 2 mg", limit=20)
                self.assertIn(
                    f"MATH-DIGIT-{index}",
                    [row["product_ref"] for row in results],
                )

    def test_text_compatibility_candidates_cover_all_nfkc_ascii_letter_forms(self) -> None:
        for index, (raw_letter, normalized_letter) in enumerate(
            (("ẖ", "h"), ("ⱼ", "j"), ("ꟲ", "c"), ("ᶜ", "c"))
        ):
            with self.subTest(raw_letter=raw_letter):
                query_text = f"x{normalized_letter} 5 mg"
                query = parse_product_search_query(query_text)
                self.assertIsNotNone(
                    match_product_fields(query, product_name=f"x{raw_letter}정5mg")
                )
                results = self.repo.search(query_text, limit=20)
                self.assertIn(
                    f"TEXT-COMPAT-{index}",
                    [row["product_ref"] for row in results],
                )

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

    def test_very_long_integer_search_does_not_depend_on_decimal_context(self) -> None:
        digits = "9" * 5000

        query = parse_product_search_query(f"nevermatch {digits}")

        self.assertEqual(query.number_tokens, (digits,))
        self.assertEqual(self.repo.search(f"nevermatch {digits}", limit=10), [])
        self.assertEqual(self.repo.search(digits, limit=10), [])

    def test_all_unicode_decimal_scripts_share_candidate_semantics(self) -> None:
        results = self.repo.search("Script 2 mg", limit=20)
        found = {row["product_ref"] for row in results}
        for index, raw_digit in enumerate(SCRIPT_TWO_DIGITS):
            with self.subTest(raw_digit=raw_digit):
                self.assertIsNotNone(
                    match_product_fields(
                        parse_product_search_query("Script 2 mg"),
                        product_name=f"Script{raw_digit}mg정",
                    )
                )
                self.assertIn(f"SCRIPT-DIGIT-{index}", found)

        patterns = {
            digit: raw_numeric_compat_glob(str(digit))
            for digit in range(10)
        }
        self.assertTrue(all(patterns.values()))
        with sqlite3.connect(":memory:") as con:
            for codepoint in range(0x110000):
                raw = chr(codepoint)
                try:
                    decimal = unicodedata.decimal(raw)
                except ValueError:
                    continue
                with self.subTest(codepoint=f"U+{codepoint:04X}"):
                    parsed = parse_product_search_query(f"Probe{raw}mg")
                    self.assertEqual(parsed.number_tokens, (str(decimal),))
                    matched = con.execute(
                        "SELECT ? GLOB ?", (raw, patterns[decimal])
                    ).fetchone()[0]
                    self.assertEqual(matched, 1)

    def test_unicode_numeric_compatibility_semantics_are_exhaustive(self) -> None:
        for codepoint in range(0x110000):
            raw = chr(codepoint)
            normalized = unicodedata.normalize("NFKC", raw)
            if normalized == raw or not any(char.isdecimal() for char in normalized):
                continue
            name = unicodedata.name(raw, "")
            expected_strength = raw.isdecimal() or (
                len(normalized) == 1
                and normalized in "0123456789"
                and ("SUPERSCRIPT" in name or "SUBSCRIPT" in name)
            )
            parsed = parse_product_search_query(f"Probe{raw}mg")
            with self.subTest(codepoint=f"U+{codepoint:04X}", name=name):
                self.assertEqual(bool(parsed.number_tokens), expected_strength)

    def test_compatibility_metadata_numbers_do_not_become_strengths(self) -> None:
        cases = (
            ("Enum 1 mg", "Enum🄂mg정"),
            ("Date 5 mg", "Date㋄mg정"),
            ("Hour 21 mg", "Hour㍭mg정"),
            ("Day 25 mg", "Day㏸mg정"),
            ("Dimension 2 mg", "Dimension㎡mg정"),
            ("Cube 3 mg", "Cube㎤mg정"),
            ("Fraction 1 2 mg", "Fraction½mg정"),
        )
        for query_text, raw_name in cases:
            with self.subTest(query=query_text, raw_name=raw_name):
                self.assertIsNone(
                    match_product_fields(
                        parse_product_search_query(query_text),
                        product_name=raw_name,
                    )
                )

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