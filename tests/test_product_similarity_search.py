from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.products import ProductRepository
from medicine_app.reference_contracts.v1 import REFERENCE_CONTRACT_MAJOR, verify_reference_database
from medicine_canonical.mobile import MOBILE_PHYSICAL_POLICY_VERSION, build_mobile_database
from tests.canonical_fixture_support import add_product
from tests.test_safety_coverage import make_canonical_db


class ProductSimilaritySearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical = root / "canonical.sqlite"
        self.mobile = root / "mobile.sqlite"
        make_canonical_db(self.canonical)
        with sqlite3.connect(self.canonical) as con:
            add_product(
                con,
                "SIM-TYLENOL-500",
                "Tylenol500mg Tablet",
                "Acetaminophen",
                manufacturer="Example Pharma",
            )
            add_product(
                con,
                "SIM-TYLENOL-50",
                "Tylenol50mg Tablet",
                "Acetaminophen",
                manufacturer="Example Pharma",
            )
            add_product(
                con,
                "SIM-VIT-E400",
                "서흥비타민E400아이유연질캡슐",
                "Tocopherol",
            )
            add_product(
                con,
                "SIM-VIT-E400-COLLISION",
                "M-Albendazole 400mg Tab",
                "Albendazole",
            )
            add_product(
                con,
                "SIM-S1",
                "아그리팔S1프리필드시린지",
                "Influenza Vaccine",
            )
            add_product(
                con,
                "SIM-S1-COLLISION",
                "니코틴엘TTS10",
                "Nicotine",
            )
            add_product(
                con,
                "SIM-PCT",
                "푸카인0.5%주사",
                "Bupivacaine Hydrochloride",
            )
            add_product(
                con,
                "SIM-MG",
                "푸카인0.5mg주사",
                "Bupivacaine Hydrochloride",
            )
            con.commit()
        self.repo = ProductRepository(self.canonical)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_canonical_schema_maintains_trigram_search_index(self) -> None:
        with sqlite3.connect(self.canonical) as con:
            fts = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='product_search_fts' AND type='table'"
            ).fetchone()
            self.assertIsNotNone(fts)
            self.assertIn("tokenize='trigram'", str(fts[0]).lower())
            ocr_fts = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='product_search_ocr_fts' AND type='table'"
            ).fetchone()
            self.assertIsNotNone(ocr_fts)
            self.assertIn("tokenize='unicode61'", str(ocr_fts[0]).lower())
            indexed = con.execute(
                "SELECT COUNT(DISTINCT item_seq) FROM product_search_fts"
            ).fetchone()[0]
            ocr_indexed = con.execute(
                "SELECT COUNT(DISTINCT item_seq) FROM product_search_ocr_fts"
            ).fetchone()[0]
            products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            self.assertEqual(indexed, products)
            self.assertEqual(ocr_indexed, products)

    def test_ocr_similarity_recovers_digit_letter_confusion_without_digit_semantics(self) -> None:
        results = self.repo.search("Tylenol50O", mode="ocr", limit=5, explain=True)
        self.assertEqual(results[0]["product_ref"], "SIM-TYLENOL-500")
        self.assertEqual(results[0]["search_match"]["tier"], "similarity")
        self.assertTrue(results[0]["search_match"]["fuzzy"])

    def test_spacing_joining_and_bare_digits_are_similarity_evidence(self) -> None:
        compact = self.repo.search("비타민E400", limit=5)
        spaced = self.repo.search("비타민 E400", limit=5)
        self.assertEqual(compact[0]["product_ref"], "SIM-VIT-E400")
        self.assertEqual(spaced[0]["product_ref"], "SIM-VIT-E400")

    def test_earlier_alphanumeric_hit_outranks_incidental_suffix_collision(self) -> None:
        e400 = self.repo.search("E400", limit=5)
        s1 = self.repo.search("S1", limit=5)
        self.assertEqual(e400[0]["product_ref"], "SIM-VIT-E400")
        self.assertEqual(s1[0]["product_ref"], "SIM-S1")

    def test_explicit_number_unit_qualifier_is_a_hard_constraint(self) -> None:
        percent = self.repo.search("푸카인 0.5%", limit=5)
        mass = self.repo.search("푸카인 0.5mg", limit=5)
        self.assertEqual(percent[0]["product_ref"], "SIM-PCT")
        self.assertEqual(mass[0]["product_ref"], "SIM-MG")
        self.assertNotIn("SIM-MG", [row["product_ref"] for row in percent])
        self.assertNotIn("SIM-PCT", [row["product_ref"] for row in mass])

    def test_mobile_physical_policy_requires_search_index(self) -> None:
        self.assertEqual(MOBILE_PHYSICAL_POLICY_VERSION, "9")
        release = build_mobile_database(self.canonical, self.mobile)
        verified = verify_reference_database(
            self.mobile,
            REFERENCE_CONTRACT_MAJOR,
            release["dataset_id"],
        )
        self.assertEqual(verified["status"], "verified")
        with sqlite3.connect(self.mobile) as con:
            con.execute("DROP TABLE product_search_ocr_fts")
            con.commit()
        with self.assertRaisesRegex(ValueError, "OCR product search index"):
            verify_reference_database(
                self.mobile,
                REFERENCE_CONTRACT_MAJOR,
                release["dataset_id"],
            )


if __name__ == "__main__":
    unittest.main()
