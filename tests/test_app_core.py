from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from medicine_app.core import MedicationApp


def make_dur_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE product_dur (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            ingredient_name TEXT,
            ingredient_code TEXT,
            product_name TEXT,
            product_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_code TEXT,
            paired_product_name TEXT,
            paired_product_code TEXT,
            rule_value TEXT,
            details TEXT,
            notice_no TEXT,
            notice_date TEXT
        );
        CREATE TABLE product_catalog (
            product_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            ingredient_code TEXT,
            ingredient_name TEXT
        );
        """
    )
    rows = [
        (
            "product:combination_contraindication", 2, "combination_contraindication",
            "drug-a", "ING-A", "약A", "P-A", "drug-b", "ING-B", "약B", "P-B",
            None, "함께 사용하지 않아야 함", "고시-1", "2026-01-01",
        ),
        (
            "product:age_contraindication", 2, "age_contraindication",
            "drug-b", "ING-B", "약B", "P-B", None, None, None, None,
            "18 세 미만", "18세 미만 안전성 미확립", "공고-1", "2026-01-01",
        ),
        (
            "product:pregnancy_contraindication", 2, "pregnancy_contraindication",
            "drug-b", "ING-B", "약B", "P-B", None, None, None, None,
            "2", "임부 사용 시 위해 가능", "공고-2", "2026-01-01",
        ),
        (
            "product:therapeutic_duplication_caution", 2, "therapeutic_duplication_caution",
            "drug-a", "ING-A", "약A", "P-A", None, None, None, None,
            "해열진통소염제", None, "공고-3", "2026-01-01",
        ),
        (
            "product:therapeutic_duplication_caution", 3, "therapeutic_duplication_caution",
            "drug-c", "ING-C", "약C", "P-C", None, None, None, None,
            "해열진통소염제", None, "공고-3", "2026-01-01",
        ),
        (
            "product:elderly_caution", 2, "elderly_caution",
            "drug-d", "ING-D", "약D", "P-D", None, None, None, None,
            None, "노인에서 주의", "공고-4", "2026-01-01",
        ),
        (
            "product:duration_caution", 2, "duration_caution",
            "drug-b", "ING-B", "약B", "P-B", None, None, None, None,
            "28", None, "공고-5", "2026-01-01",
        ),
    ]
    con.executemany(
        """
        INSERT INTO product_dur (
            dataset_key, source_row, category, ingredient_name, ingredient_code,
            product_name, product_code, paired_ingredient_name, paired_ingredient_code,
            paired_product_name, paired_product_code, rule_value, details, notice_no, notice_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.execute(
        """
        INSERT OR IGNORE INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name)
        SELECT product_code,product_name,ingredient_code,ingredient_name
        FROM product_dur WHERE product_code IS NOT NULL
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name)
        SELECT paired_product_code,paired_product_name,paired_ingredient_code,paired_ingredient_name
        FROM product_dur WHERE paired_product_code IS NOT NULL
        """
    )
    con.commit()
    con.close()


class MedicationAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_dur_db(self.dur_db)
        self.app = MedicationApp(self.dur_db, self.personal_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_manages_multiple_people_and_separate_medication_lists(self) -> None:
        alice = self.app.create_person("Alice", "1990-04-03", "female", "not_pregnant")
        bob = self.app.create_person("Bob", "1988-09-11", "male", "not_applicable")

        self.app.add_medication(alice["id"], product_code="P-A", dosage_text="1정", schedule_times=["08:00"])

        self.assertEqual([p["name"] for p in self.app.list_people()], ["Alice", "Bob"])
        self.assertEqual(len(self.app.list_medications(alice["id"])), 1)
        self.assertEqual(self.app.list_medications(bob["id"]), [])

    def test_preview_combines_current_medications_age_and_pregnancy(self) -> None:
        person = self.app.create_person("Teen", "2010-01-10", "female", "pregnant")
        self.app.add_medication(person["id"], product_code="P-A")

        preview = self.app.preview_medication(person["id"], "P-B", as_of=date(2026, 8, 9))
        risk_types = {risk["type"] for risk in preview["risks"]}

        self.assertIn("combination_contraindication", risk_types)
        self.assertIn("age_contraindication", risk_types)
        self.assertIn("pregnancy_contraindication", risk_types)
        self.assertIn("duration_caution", risk_types)

    def test_detects_therapeutic_duplication_and_elderly_caution(self) -> None:
        older = self.app.create_person("Older", "1940-02-01", "female", "not_pregnant")
        self.app.add_medication(older["id"], product_code="P-A")

        duplicate = self.app.preview_medication(older["id"], "P-C", as_of=date(2026, 8, 9))
        self.assertIn("therapeutic_duplication_caution", {r["type"] for r in duplicate["risks"]})

        elderly = self.app.preview_medication(older["id"], "P-D", as_of=date(2026, 8, 9))
        self.assertIn("elderly_caution", {r["type"] for r in elderly["risks"]})

    def test_records_dose_history(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        med = self.app.add_medication(person["id"], product_code="P-A", schedule_times=["08:00", "20:00"])

        log = self.app.record_dose(med["id"], "taken", "2026-08-09T08:03:00+09:00")
        history = self.app.list_dose_logs(person["id"])

        self.assertEqual(log["status"], "taken")
        self.assertEqual(history[0]["medication_id"], med["id"])

    def test_default_dose_timestamp_uses_korea_timezone(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        med = self.app.add_medication(person["id"], product_code="P-A")

        log = self.app.record_dose(med["id"], "taken")

        self.assertTrue(log["occurred_at"].endswith("+09:00"))

    def test_product_search_returns_both_sides_of_combination_rows_without_duplicates(self) -> None:
        results = self.app.search_products("약", limit=10)
        by_code = {row["product_code"] for row in results}
        self.assertTrue({"P-A", "P-B", "P-C", "P-D"}.issubset(by_code))

    def test_product_search_collapses_same_product_code_across_dur_categories(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            """
            INSERT INTO product_dur (
                dataset_key, source_row, category, ingredient_name, ingredient_code,
                product_name, product_code, rule_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("product:pregnancy", 99, "pregnancy_contraindication", "Drug A Alias", "ING-A", "약A", "P-A", "2"),
        )
        con.commit()
        con.close()

        results = self.app.search_products("약A", limit=10)

        self.assertEqual([row["product_code"] for row in results].count("P-A"), 1)


if __name__ == "__main__":
    unittest.main()
