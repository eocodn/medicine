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


def make_catalog_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE products (
            item_seq TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            manufacturer TEXT,
            ingredient_name TEXT,
            dosage_form TEXT,
            edi_code TEXT,
            permit_date TEXT,
            cancel_date TEXT,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )
    con.executemany(
        """
        INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_name,dosage_form,
            edi_code,permit_date,cancel_date,source,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ("MFDS-B", "전체카탈로그약B", "제약B", "drug-b", "정제", "P-B", "2020-01-01", None, "mfds", "{}"),
            ("MFDS-X", "비급여전체약X", "제약X", "drug-x", "캡슐", None, "2021-01-01", None, "mfds", "{}"),
        ],
    )
    con.commit()
    con.close()


class MedicationAppTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.app = MedicationApp(self.dur_db, self.personal_db, self.catalog_db)

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
    def test_full_catalog_search_maps_mfds_item_to_dur_code_when_available(self) -> None:
        results = self.app.search_products("전체카탈로그약B", limit=10)
        self.assertEqual(results[0]["product_ref"], "MFDS-B")
        self.assertEqual(results[0]["product_code"], "P-B")
        self.assertTrue(results[0]["dur_match"])
        self.assertEqual(results[0]["catalog_source"], "mfds")

        unmatched = self.app.search_products("비급여전체약X", limit=10)[0]
        self.assertEqual(unmatched["product_ref"], "MFDS-X")
        self.assertIsNone(unmatched["product_code"])
        self.assertFalse(unmatched["dur_match"])

    def test_adds_structured_prescription_and_computes_end_date(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        med = self.app.add_medication(
            person["id"],
            product_ref="MFDS-B",
            dose_amount=1,
            dose_unit="정",
            frequency_per_day=3,
            meal_relation="after_meal",
            administration_route="oral",
            prescription_days=5,
            start_date="2026-08-10",
            schedule_times=["08:00", "13:00", "19:00"],
        )

        self.assertEqual(med["catalog_item_seq"], "MFDS-B")
        self.assertEqual(med["product_code"], "P-B")
        self.assertEqual(med["dose_amount"], 1.0)
        self.assertEqual(med["dose_unit"], "정")
        self.assertEqual(med["frequency_per_day"], 3)
        self.assertEqual(med["meal_relation"], "after_meal")
        self.assertEqual(med["administration_route"], "oral")
        self.assertEqual(med["prescription_days"], 5)
        self.assertEqual(med["end_date"], "2026-08-14")

    def test_daily_plan_is_idempotent_and_tracks_instance_completion(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        self.app.add_medication(
            person["id"],
            product_code="P-A",
            dose_amount=1,
            dose_unit="정",
            frequency_per_day=2,
            start_date="2026-08-10",
            prescription_days=3,
            schedule_times=["08:00", "20:00"],
        )

        first = self.app.get_daily_plan(person["id"], "2026-08-10")
        second = self.app.get_daily_plan(person["id"], "2026-08-10")
        self.assertEqual(len(first["doses"]), 2)
        self.assertEqual([dose["id"] for dose in first["doses"]], [dose["id"] for dose in second["doses"]])
        self.assertTrue(all(dose["status"] == "planned" for dose in first["doses"]))

        completed = self.app.record_dose_instance(first["doses"][0]["id"], "taken", "2026-08-10T08:05:00+09:00")
        refreshed = self.app.get_daily_plan(person["id"], "2026-08-10")
        self.assertEqual(completed["status"], "taken")
        self.assertEqual(refreshed["doses"][0]["status"], "taken")
        self.assertEqual(len(self.app.list_dose_logs(person["id"])), 1)
        self.assertEqual(self.app.get_daily_plan(person["id"], "2026-08-13")["doses"], [])

    def test_daily_plan_uses_unscheduled_frequency_slots_and_separates_prn(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        self.app.add_medication(person["id"], product_code="P-A", frequency_per_day=3, start_date="2026-08-10")
        prn = self.app.add_medication(
            person["id"], product_code="P-B", as_needed=True, dose_amount=1, dose_unit="정", start_date="2026-08-10"
        )

        plan = self.app.get_daily_plan(person["id"], "2026-08-10")
        self.assertEqual(len(plan["doses"]), 3)
        self.assertTrue(all(dose["scheduled_time"] is None for dose in plan["doses"]))
        self.assertEqual([dose["slot_label"] for dose in plan["doses"]], ["1회차", "2회차", "3회차"])
        self.assertEqual([med["id"] for med in plan["prn_medications"]], [prn["id"]])

    def test_migrates_legacy_personal_database_without_losing_medication(self) -> None:
        legacy = Path(self.tmp.name) / "legacy.sqlite"
        con = sqlite3.connect(legacy)
        con.executescript(
            """
            CREATE TABLE people (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, birth_date TEXT NOT NULL,
                sex TEXT NOT NULL, pregnancy_status TEXT NOT NULL, notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE medications (
                id TEXT PRIMARY KEY, person_id TEXT NOT NULL, product_code TEXT,
                product_name TEXT NOT NULL, ingredient_code TEXT, ingredient_name TEXT,
                dosage_text TEXT, start_date TEXT, end_date TEXT, active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'dur_search', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE medication_schedules (
                id TEXT PRIMARY KEY, medication_id TEXT NOT NULL, time_of_day TEXT NOT NULL,
                dose_text TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE dose_logs (
                id TEXT PRIMARY KEY, medication_id TEXT NOT NULL, person_id TEXT NOT NULL,
                status TEXT NOT NULL, occurred_at TEXT NOT NULL, note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO people(id,name,birth_date,sex,pregnancy_status) VALUES('person-1','기존','1990-01-01','female','not_pregnant');
            INSERT INTO medications(id,person_id,product_code,product_name) VALUES('med-1','person-1','P-A','약A');
            """
        )
        con.commit()
        con.close()

        migrated = MedicationApp(self.dur_db, legacy, self.catalog_db)
        med = migrated.get_medication("med-1")
        self.assertEqual(med["product_name"], "약A")
        self.assertIn("frequency_per_day", med)
        self.assertFalse(med["as_needed"])


if __name__ == "__main__":
    unittest.main()
