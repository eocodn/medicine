from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import MedicationApp
from medicine_app.product_search import (
    match_product_fields,
    parse_product_search_query,
    raw_candidate_variants,
)
from medicine_app.products import ProductRepository
from medicine_canonical.mobile import build_mobile_database
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db
from tests.test_safety_coverage import make_canonical_db as make_release_canonical_db


class ProductSearchQueryTest(unittest.TestCase):
    def test_normalizes_spacing_boundaries_and_strength_units(self) -> None:
        spaced = parse_product_search_query(" 씬지록신 25 ㎍ ")
        compact = parse_product_search_query("씬지록신25mcg")
        ascii_before_form = parse_product_search_query("알로판400mg정")
        thousands = parse_product_search_query("글루파 1,000 mg")

        self.assertEqual(spaced.text_tokens, ("씬지록신",))
        self.assertEqual(spaced.number_tokens, ("25",))
        self.assertEqual(spaced.unit_tokens, ("ug",))
        self.assertEqual(compact.text_tokens, spaced.text_tokens)
        self.assertEqual(compact.number_tokens, spaced.number_tokens)
        self.assertEqual(compact.unit_tokens, spaced.unit_tokens)
        self.assertEqual(ascii_before_form.text_tokens, ("알로판", "정"))
        self.assertEqual(ascii_before_form.number_tokens, ("400",))
        self.assertEqual(ascii_before_form.unit_tokens, ("mg",))
        self.assertEqual(thousands.number_tokens, ("1000",))
        self.assertEqual(thousands.unit_tokens, ("mg",))

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

    def test_raw_candidate_variants_cover_nfkc_compatibility_equivalents(self) -> None:
        self.assertIn("Ⅱ", raw_candidate_variants("ii"))
        self.assertIn("ⅱ", raw_candidate_variants("ii"))
        self.assertIn("１", raw_candidate_variants("1"))
        self.assertNotIn("①", raw_candidate_variants("1"))
        self.assertIn("fvⅢ", raw_candidate_variants("fviii"))

    def test_compatibility_units_and_enclosed_markers_keep_search_semantics(self) -> None:
        parsed = parse_product_search_query("약150㎎①수출명150㎎Capsule②")

        self.assertEqual(parsed.number_tokens, ("150", "150"))
        self.assertEqual(parsed.unit_tokens, ("mg", "mg"))
        self.assertEqual(parsed.text_tokens, ("약", "수출명", "capsule"))

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
            add_product(
                con,
                "ALLOPAN-400",
                "알로판400mg정(이부프로펜)",
                "Ibuprofen",
            )
            add_product(
                con,
                "DANAZOL-200",
                "영풍다나졸200mg캡슐",
                "Danazol",
            )
            add_product(
                con,
                "GLUPA-COMMA",
                "글루파정1,000㎎(메트포르민염산염)",
                "Metformin Hydrochloride",
            )
            add_product(
                con,
                "D3-COMMA",
                "디3 베이스 경구 드롭스 10,000IU/mL (콜레칼시페롤)",
                "Cholecalciferol",
            )
            add_product(
                con,
                "ROMAN-II",
                "지로티프Ⅱ주(정제브이아이장티푸스백신)",
                "Typhoid Vaccine",
            )
            add_product(
                con,
                "CIRCLED-EXPORT",
                "코러스시프로플록사신정500밀리그람(수출명①시프로플록사신정500mg②Super-cipro)(수출용)",
                "Ciprofloxacin",
            )
            add_product(
                con,
                "SQUARE-UNIT-LATIN",
                "동구염산클린다마이신캅셀150㎎[수출명:DongkooClindamycin150㎎Capsule]",
                "Clindamycin",
            )
            add_product(
                con,
                "EMBEDDED-ROMAN",
                "그린모노주250단위(건조FVⅢ:C단클론항체정제사람혈액응고제VⅢ:C인자)",
                "Coagulation Factor VIII",
            )
            add_product(
                con,
                "ARONAMIN-GOLD",
                "아로나민골드정",
                "Vitamin Complex",
            )
            add_product(
                con,
                "MOATAMIN-GOLD",
                "모아타민골드비백정",
                "Vitamin Complex",
            )
            add_product(
                con,
                "DOT-MIXTURE",
                "혼합성분정",
                "Pelargonium Sidoides 11% Ethanol Extract (1→8~10)·Glycerin Mixed Solution (8:2)",
            )
            add_product(
                con,
                "NESTED-SLASH",
                "괄호성분정",
                "Human Erythropoietin (rDNA/ Vector; Host: CH0 dhfr-ATCC CRL9096)/Glycerin 7",
            )
            add_product(
                con,
                "CROSS-FIELD",
                "Borrower정",
                "Unrelated Ingredient 5",
            )
            add_product(
                con,
                "FULLWIDTH-STRENGTH",
                "풀위드정５밀리그램",
                "Fullwidth Ingredient",
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

    def test_structured_ingredient_numbers_stay_bound_to_the_same_component(self) -> None:
        self.assertEqual(self.app.search_products("Cellulase 500", limit=10), [])
        self.assertEqual(self.app.search_products("Lipase 500", limit=10), [])
        self.assert_first("Biodiastase 500", "DIGEST-500")

    def test_ingredient_component_matching_respects_top_level_composition_boundaries(self) -> None:
        self.assertEqual(self.app.search_products("Glycerin 11", limit=10), [])
        self.assert_first("Glycerin 8", "DOT-MIXTURE")
        self.assert_first("Human Erythropoietin 9096", "NESTED-SLASH")

    def test_structured_candidate_generation_does_not_borrow_numbers_across_fields(self) -> None:
        query = parse_product_search_query("Borrower 5")
        with sqlite3.connect(self.canonical_db) as con:
            con.row_factory = sqlite3.Row
            rows = ProductRepository._structured_candidate_rows(con, query, False)

        self.assertNotIn("CROSS-FIELD", [row["item_seq"] for row in rows])

    def test_ascii_units_before_hangul_forms_and_thousands_are_normalized(self) -> None:
        self.assert_first("알로판 400mg", "ALLOPAN-400")
        self.assert_first("알로판 400 밀리그램", "ALLOPAN-400")
        self.assert_first("영풍다나졸 200 mg", "DANAZOL-200")
        self.assert_first("글루파 1000 mg", "GLUPA-COMMA")
        self.assert_first("글루파 1,000 mg", "GLUPA-COMMA")
        self.assert_first("디3 베이스 10000", "D3-COMMA")
        self.assert_first("풀위드 5", "FULLWIDTH-STRENGTH")

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
        self.assert_first("씬록신 25", "SYN-25", mode="ocr")
        self.assert_first("씬지지록신 25", "SYN-25", mode="ocr")
        self.assert_first("아로민골드", "ARONAMIN-GOLD", mode="ocr")
        self.assertEqual(self.app.search_products("타징 서방징 10 5", limit=10, mode="ocr"), [])

        result = self.app.search_products("씬지록심 25", limit=10, mode="ocr")[0]
        self.assertEqual(result["product_mapping_method"], "item_seq_exact")
        self.assertNotIn("search_match", result)

    def test_search_uses_normalized_mode_after_parsing(self) -> None:
        self.assert_first("씬즈록신 25", "SYN-25", mode="OCR")
        self.assert_first("씬즈록신 25", "SYN-25", mode=" ocr ")

    def test_nfkc_equivalent_product_text_survives_candidate_generation(self) -> None:
        self.assert_first("지로티프Ⅱ", "ROMAN-II")
        self.assert_first("지로티프 II", "ROMAN-II")

    def test_nfkc_equivalent_target_is_not_hidden_by_raw_ascii_candidate(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            add_product(
                con,
                "ROMAN-DISTRACTOR",
                "지로티프II가짜정",
                "Distractor",
            )
        results = self.app.search_products("지로티프 II", limit=10)
        refs = [row["product_ref"] for row in results]

        self.assertIn("ROMAN-II", refs)
        self.assertIn("ROMAN-DISTRACTOR", refs)

    def test_nfkc_derived_numeric_tokens_retrieve_exact_circled_digit_product_name(self) -> None:
        exact_name = "코러스시프로플록사신정500밀리그람(수출명①시프로플록사신정500mg②Super-cipro)(수출용)"
        results = self.app.search_products(exact_name, limit=10)

        self.assertIn("CIRCLED-EXPORT", [row["product_ref"] for row in results])

    def test_nfkc_compatibility_spans_inside_text_tokens_retrieve_exact_product_name(self) -> None:
        square_unit = "동구염산클린다마이신캅셀150㎎[수출명:DongkooClindamycin150㎎Capsule]"
        embedded_roman = "그린모노주250단위(건조FVⅢ:C단클론항체정제사람혈액응고제VⅢ:C인자)"

        self.assertIn(
            "SQUARE-UNIT-LATIN",
            [row["product_ref"] for row in self.app.search_products(square_unit, limit=10)],
        )
        self.assertIn(
            "EMBEDDED-ROMAN",
            [row["product_ref"] for row in self.app.search_products(embedded_roman, limit=10)],
        )

    def test_explain_is_present_for_structured_legacy_and_identifier_paths(self) -> None:
        structured = self.app.search_products("씬지록신 25", limit=10, explain=True)[0]
        legacy = self.app.search_products("알프람", limit=10, explain=True)[0]
        identifier = self.app.search_products("SYN-25", limit=10, explain=True)[0]

        for result in (structured, legacy, identifier):
            self.assertIn("search_match", result)
            self.assertIn("field", result["search_match"])
            self.assertIn("tier", result["search_match"])
            self.assertIn("fuzzy", result["search_match"])
            self.assertIn("sort_key", result["search_match"])

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