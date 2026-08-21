from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import MedicationApp
from medicine_app.product_search import (
    match_product_fields,
    parse_product_search_query,
)
from medicine_app.products import ProductRepository
from medicine_canonical.mobile import build_mobile_database
from tests.canonical_fixture_support import add_product
from tests.test_app_core import make_canonical_db
from tests.test_safety_coverage import make_canonical_db as make_release_canonical_db


class ProductSearchQueryTest(unittest.TestCase):
    def test_bare_digits_remain_orthographic_text(self) -> None:
        b12 = parse_product_search_query("Vitamin B12")
        e400 = parse_product_search_query("비타민 E400")
        tylenol = parse_product_search_query("Tylenol500")

        self.assertEqual(b12.normalized, "vitaminb12")
        self.assertEqual(e400.normalized, "비타민e400")
        self.assertEqual(tylenol.normalized, "tylenol500")
        self.assertEqual(b12.explicit_qualifiers, ())
        self.assertEqual(e400.explicit_qualifiers, ())
        self.assertEqual(tylenol.explicit_qualifiers, ())

    def test_only_explicit_known_units_create_hard_qualifiers(self) -> None:
        micro = parse_product_search_query("씬지록신 25 ㎍")
        concentration = parse_product_search_query("보령듀리세프 125mg/5ml")
        shared = parse_product_search_query("타진 10/5mg")
        korean = parse_product_search_query("그린진 500 단위")

        self.assertEqual(micro.explicit_qualifiers, (("25", "ug"),))
        self.assertEqual(concentration.explicit_qualifiers, (("125", "mg"), ("5", "ml")))
        self.assertEqual(shared.explicit_qualifiers, (("10", "mg"), ("5", "mg")))
        self.assertEqual(korean.explicit_qualifiers, (("500", "iu"),))

    def test_ocr_mode_strips_trailing_regimen_without_product_specific_rules(self) -> None:
        query = parse_product_search_query("타진서방정 10/5mg0.5정", mode="ocr")
        self.assertEqual(query.normalized, parse_product_search_query("타진서방정 10/5mg").normalized)
        self.assertEqual(query.explicit_qualifiers, (("10", "mg"), ("5", "mg")))

    def test_identifier_shape_is_kept_as_separate_high_priority_path(self) -> None:
        self.assertTrue(parse_product_search_query("EDI-SYN-25").identifier_like)
        self.assertTrue(parse_product_search_query("SYN-25").identifier_like)
        self.assertFalse(parse_product_search_query("씬지록신 25").identifier_like)
        self.assertFalse(parse_product_search_query("Tylenol 500").identifier_like)


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
                "SYN-25-ALT",
                "AAA Identifier Prefix Collision",
                "Synthetic Ingredient",
                edi="EDI-SYN-25-ALT",
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
                "TAJIN-5-2.5",
                "타진서방정 5/2.5mg",
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
            add_product(
                con,
                "FULLWIDTH-LATIN-MIXED",
                "ＡｂＣ정５mg",
                "Fullwidth Latin Ingredient",
            )
            add_product(
                con,
                "CEF-125-5",
                "보령듀리세프건조시럽125밀리그램/5밀리리터(세파드록실수화물)",
                "Cefadroxil Hydrate",
            )
            add_product(
                con,
                "CEF-250-5",
                "보령듀리세프건조시럽250밀리그램/5밀리리터(세파드록실수화물)",
                "Cefadroxil Hydrate",
            )
            add_product(
                con,
                "COMBO-REPEATED-UNIT",
                "콤보정10mg/5mg",
                "Alpha 10mg/Beta 5mg",
            )
            add_product(
                con,
                "SHARED-TRIPLE",
                "스타레보필름코팅정50/12.5/200밀리그램",
                "Levodopa/Carbidopa/Entacapone",
            )
            add_product(
                con,
                "MALFORMED-COMPONENTS",
                "플레인연조엑스",
                "Angelica Gigas Root Soft Extract (2.8∼3.4→1)/Cassiae Cortex InteriorExtract(8.3∼12.5⟶1/Cyperus Rhizome Soft Extract (2.8~3.5→1)/Licorice Soft Extract(2.1~2.5→1)/Sappan Wood Extract(7.6∼11.4⟶1)",
            )
        self.app = MedicationApp(self.canonical_db, self.personal_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def assert_first(self, query: str, product_ref: str, *, mode: str = "manual") -> None:
        results = self.app.search_products(query, limit=10, mode=mode)
        self.assertTrue(results, query)
        self.assertEqual(results[0]["product_ref"], product_ref, query)
    def test_similarity_search_handles_spacing_forms_and_bare_digits(self) -> None:
        self.assert_first("씬지록신 25", "SYN-25")
        self.assert_first("씬지록신25", "SYN-25")
        self.assert_first("가바뉴로 25", "GABA-25")
        self.assert_first("타진 10 5", "TAJIN-10-5")
        self.assert_first("알프람 0.25", "ALPRAM-025")

        refs = [row["product_ref"] for row in self.app.search_products("씬지록신 25", limit=10)]
        self.assertEqual(refs[0], "SYN-25")
        # Bare 25 is character evidence, not a hard strength constraint.
        self.assertIn("SYN-125", refs)

    def test_explicit_units_are_hard_constraints(self) -> None:
        self.assert_first("씬지록신 25 mcg", "SYN-25")
        self.assertNotIn(
            "SYN-125",
            [row["product_ref"] for row in self.app.search_products("씬지록신 25 mcg", limit=10)],
        )
        self.assertEqual(self.app.search_products("보령듀리세프건조시럽 5 mg", limit=10), [])
        self.assertEqual(self.app.search_products("보령듀리세프건조시럽 250 ml", limit=10), [])
        self.assert_first("보령듀리세프건조시럽 5 ml", "CEF-125-5")

    def test_shared_unit_and_component_constraints_remain_field_local(self) -> None:
        self.assert_first("타진 10/5mg", "TAJIN-10-5")
        self.assert_first("스타레보 50/12.5/200mg", "SHARED-TRIPLE")
        self.assert_first("Pelargonium 11%", "DOT-MIXTURE")
        self.assertEqual(self.app.search_products("Glycerin 11%", limit=10), [])
        self.assert_first("Human Erythropoietin CRL9096", "NESTED-SLASH")

    def test_ingredient_and_manufacturer_fallback_share_same_similarity_core(self) -> None:
        self.assert_first("Biodiastase 500", "DIGEST-500")
        results = self.app.search_products("Sodium Lactate Solution 50%", limit=10)
        self.assertIn("HARTMANN", [row["product_ref"] for row in results])
        manufacturer = ProductRepository(self.canonical_db).search(
            "부광약품", limit=10, explain=True
        )
        self.assertEqual(
            {row["product_ref"] for row in manufacturer[:2]},
            {"SYN-25", "SYN-125"},
        )
        self.assertTrue(all(row["search_match"]["field"] == "manufacturer" for row in manufacturer[:2]))

    def test_nfkc_unit_and_case_variants_remain_candidate_complete(self) -> None:
        self.assert_first("알로판 400 mg", "ALLOPAN-400")
        self.assert_first("글루파 1000 mg", "GLUPA-COMMA")
        self.assert_first("디3 베이스 10000", "D3-COMMA")
        self.assert_first("지로티프II", "ROMAN-II")
        self.assert_first("DongkooClindamycin 150 mg Capsule", "SQUARE-UNIT-LATIN")
        self.assert_first("ABC 5 mg", "FULLWIDTH-LATIN-MIXED")

    def test_ocr_similarity_recovers_one_character_errors_and_trailing_regimen(self) -> None:
        self.assertEqual(self.app.search_products("씬즈록신 25", limit=10), [])
        self.assert_first("씬즈록신 25", "SYN-25", mode="ocr")
        self.assert_first("타진서방정 10/5mg0.5정", "TAJIN-10-5", mode="ocr")
        explained = ProductRepository(self.canonical_db).search(
            "씬즈록신 25", limit=10, mode="ocr", explain=True
        )
        self.assertEqual(explained[0]["search_match"]["field"], "product_name")
        self.assertEqual(explained[0]["search_match"]["tier"], "similarity")
        self.assertTrue(explained[0]["search_match"]["fuzzy"])

    def test_exact_identifier_is_prioritized_over_prefix_collisions(self) -> None:
        repo = ProductRepository(self.canonical_db)
        item_seq = repo.search("SYN-25", limit=10, explain=True)
        edi = repo.search("EDI-SYN-25", limit=10, explain=True)
        self.assertEqual(item_seq[0]["product_ref"], "SYN-25")
        self.assertEqual(edi[0]["product_ref"], "SYN-25")
        self.assertEqual(item_seq[0]["search_match"]["tier"], "identifier_exact")
        self.assertEqual(edi[0]["search_match"]["tier"], "identifier_exact")

    def test_search_index_is_required_in_canonical_and_mobile_physical_layouts(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='product_search_fts'"
            ).fetchone()[0]
            self.assertIn("trigram", sql.lower())

        release_canonical = Path(self.tmp.name) / "release-canonical.sqlite"
        mobile = Path(self.tmp.name) / "mobile.sqlite"
        make_release_canonical_db(release_canonical)
        build_mobile_database(release_canonical, mobile)
        with sqlite3.connect(mobile) as con:
            sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='product_search_fts'"
            ).fetchone()[0]
            self.assertIn("trigram", sql.lower())

    def test_long_query_is_bounded_and_returns_no_false_match(self) -> None:
        digits = "9" * 5000
        self.assertEqual(self.app.search_products(f"nevermatch {digits}", limit=10), [])
        self.assertEqual(self.app.search_products(digits, limit=10), [])


if __name__ == "__main__":
    unittest.main()
