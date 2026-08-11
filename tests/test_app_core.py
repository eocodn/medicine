from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from medicine_app.core import MedicationApp
from medicine_app.safety import age_rule_matches
from tests.dur_fixture_support import install_verified_dur_fixture_metadata


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
    install_verified_dur_fixture_metadata(
        con, ingredients=["drug-a", "drug-b", "drug-c", "drug-d"]
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
            cancel_name TEXT,
            permit_status TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )
    con.executemany(
        """
        INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_name,dosage_form,
            edi_code,permit_date,cancel_date,cancel_name,permit_status,source,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ("MFDS-A", "약A", "제약A", "drug-a", "정제", "P-A", "2020-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-B", "전체카탈로그약B", "제약B", "drug-b", "정제", "P-B", "2020-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-C", "약C", "제약C", "drug-c", "정제", "P-C", "2020-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-D", "약D", "제약D", "drug-d", "정제", "P-D", "2020-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-X", "비급여전체약X", "제약X", "drug-x", "캡슐", None, "2021-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-Y", "비급여전체약Y", "제약Y", "drug-y", "정제", None, "2021-01-01", None, "정상", "active", "mfds", "{}"),
            ("MFDS-W", "과거취하약", "제약W", "drug-w", "정제", "P-W", "2019-01-01", "2025-07-01", "취하", "withdrawn", "mfds", "{}"),
        ],
    )
    con.commit()
    con.close()


class AgeRuleTest(unittest.TestCase):
    def test_day_based_age_rule_uses_exact_calendar_days(self) -> None:
        self.assertTrue(age_rule_matches("2026-08-01", "28일 미만", date(2026, 8, 28)))
        self.assertFalse(age_rule_matches("2026-08-01", "28일 미만", date(2026, 8, 29)))


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

    def test_profile_reproductive_status_is_normalized_and_editable(self) -> None:
        male = self.app.create_person(
            "Male", "1990-01-01", "male", "pregnant", lactation_status="breastfeeding"
        )
        self.assertEqual(male["pregnancy_status"], "not_applicable")
        self.assertEqual(male["lactation_status"], "not_applicable")

        female = self.app.create_person(
            "Female", "1990-01-01", "female", "not_pregnant", lactation_status="unknown"
        )
        updated = self.app.update_person(
            female["id"], "Female", "1990-01-01", "female", "not_pregnant", "breastfeeding"
        )
        self.assertEqual(updated["lactation_status"], "breastfeeding")

    def test_delete_person_erases_all_dependent_personal_records(self) -> None:
        person = self.app.create_person("Delete", "1990-01-01", "male")
        medication = self.app.add_medication(
            person["id"], product_code="P-A", schedule_times=["08:00"], start_date="2026-08-11",
            request_id="delete-person-med"
        )
        plan = self.app.get_daily_plan(person["id"], "2026-08-11")
        self.app.record_dose_instance(
            plan["doses"][0]["id"], "taken", "2026-08-11T08:05:00+09:00"
        )

        deleted = self.app.delete_person(person["id"])

        self.assertEqual(deleted, {"id": person["id"], "deleted": True})
        with self.assertRaises(KeyError):
            self.app.get_person(person["id"])
        con = sqlite3.connect(self.personal_db)
        try:
            for table in (
                "people", "medications", "medication_schedules", "dose_instances",
                "dose_logs", "medication_revisions", "medication_requests",
            ):
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
        finally:
            con.close()

    def test_preview_combines_current_medications_age_and_pregnancy(self) -> None:
        person = self.app.create_person("Teen", "2010-01-10", "female", "pregnant")
        current_preview = self.app.preview_medication(person["id"], "P-A")
        self.app.add_medication(
            person["id"], product_code="P-A", acknowledge_warnings=True,
            warning_token=current_preview["warning_token"],
        )

        preview = self.app.preview_medication(person["id"], "P-B", as_of=date(2026, 8, 9))
        risk_types = {risk["type"] for risk in preview["risks"]}

        self.assertIn("combination_contraindication", risk_types)
        self.assertIn("age_contraindication", risk_types)
        self.assertIn("pregnancy_contraindication", risk_types)
        self.assertNotIn("duration_caution", risk_types)
        duration = next(
            item for item in preview["dur_checks"] if item["category"] == "duration_caution"
        )
        self.assertEqual(duration["status"], "unknown")

    def test_interactions_follow_prescription_course_overlap_not_active_flag(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        self.app.add_medication(
            person["id"], product_code="P-A", start_date="2026-08-01", prescription_days=7,
        )

        separated = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "start_date": "2026-08-20", "prescription_days": 3},
        )
        overlapping = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "start_date": "2026-08-07", "prescription_days": 3},
        )

        self.assertNotIn("combination_contraindication", {r["type"] for r in separated["risks"]})
        self.assertIn("combination_contraindication", {r["type"] for r in overlapping["risks"]})

    def test_product_washout_extends_interaction_after_source_course(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            """UPDATE product_dur
               SET details='drug-a 투여 중 및 종료 후 7일 간 해당 성분 투여 금기'
               WHERE category='combination_contraindication' AND product_code='P-A'"""
        )
        con.commit()
        con.close()
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        self.app.add_medication(
            person["id"], product_code="P-A", start_date="2026-08-01", prescription_days=7,
        )

        within = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "start_date": "2026-08-14", "prescription_days": 2},
        )
        outside = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "start_date": "2026-08-15", "prescription_days": 2},
        )

        washout = [r for r in within["risks"] if r["type"] == "combination_contraindication"]
        self.assertEqual(len(washout), 1)
        self.assertEqual(washout[0]["timing"]["kind"], "washout_after")
        self.assertEqual(washout[0]["timing"]["hours"], 7 * 24)
        self.assertNotIn("combination_contraindication", {r["type"] for r in outside["risks"]})

    def test_explicitly_stopped_medication_still_contributes_during_washout(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            """UPDATE product_dur
               SET details='drug-a 투여 중 및 종료 후 7일 간 해당 성분 투여 금기'
               WHERE category='combination_contraindication' AND product_code='P-A'"""
        )
        con.commit()
        con.close()
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        medication = self.app.add_medication(person["id"], product_code="P-A", start_date="2026-08-01")
        stopped = self.app.stop_medication(medication["id"], expected_revision=medication["revision"])
        stopped_on = date.fromisoformat(stopped["stopped_at"])

        within = self.app.preview_medication(
            person["id"],
            {
                "product_code": "P-B",
                "start_date": (stopped_on + timedelta(days=7)).isoformat(),
                "prescription_days": 1,
            },
        )
        outside = self.app.preview_medication(
            person["id"],
            {
                "product_code": "P-B",
                "start_date": (stopped_on + timedelta(days=8)).isoformat(),
                "prescription_days": 1,
            },
        )

        self.assertIn("combination_contraindication", {r["type"] for r in within["risks"]})
        self.assertNotIn("combination_contraindication", {r["type"] for r in outside["risks"]})

    def test_stopped_unmapped_medication_overlapping_candidate_keeps_interaction_checks_unknown(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        draft = {
            "product_ref": "MFDS-X",
            "start_date": "2026-08-01",
            "prescription_days": 10,
        }
        preview = self.app.preview_medication(person["id"], draft)
        medication = self.app.add_medication(
            person["id"],
            **draft,
            acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )
        self.app.stop_medication(medication["id"], expected_revision=medication["revision"])

        candidate = self.app.preview_medication(
            person["id"],
            {"product_ref": "MFDS-A", "start_date": "2026-08-05", "prescription_days": 2},
        )
        checks = {item["category"]: item for item in candidate["dur_checks"]}

        for category in ("combination_contraindication", "therapeutic_duplication_caution"):
            self.assertEqual(checks[category]["status"], "unknown")
            self.assertEqual(checks[category]["summary"], "비급여전체약X 확인 필요")
            self.assertIn("비급여전체약X의 제품·성분 DUR 연결", checks[category]["details"])

    def test_multiple_unmapped_current_medications_are_named_in_interaction_unknown_details(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        for product_ref in ("MFDS-X", "MFDS-Y"):
            preview = self.app.preview_medication(
                person["id"],
                {"product_ref": product_ref, "start_date": "2026-08-01", "prescription_days": 10},
            )
            self.app.add_medication(
                person["id"],
                product_ref=product_ref,
                start_date="2026-08-01",
                prescription_days=10,
                acknowledge_warnings=True,
                warning_token=preview["warning_token"],
            )

        candidate = self.app.preview_medication(
            person["id"],
            {"product_ref": "MFDS-A", "start_date": "2026-08-05", "prescription_days": 2},
        )
        checks = {item["category"]: item for item in candidate["dur_checks"]}

        for category in ("combination_contraindication", "therapeutic_duplication_caution"):
            self.assertEqual(checks[category]["status"], "unknown")
            self.assertEqual(checks[category]["summary"], "현재 복용약 2개 확인 필요")
            self.assertIn("비급여전체약X (제품·성분)", checks[category]["details"])
            self.assertIn("비급여전체약Y (제품·성분)", checks[category]["details"])

    def test_therapeutic_duplication_also_requires_course_overlap(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        self.app.add_medication(
            person["id"], product_code="P-A", start_date="2026-08-01", prescription_days=7,
        )

        separated = self.app.preview_medication(
            person["id"],
            {"product_code": "P-C", "start_date": "2026-08-20", "prescription_days": 3},
        )

        self.assertNotIn("therapeutic_duplication_caution", {r["type"] for r in separated["risks"]})

    def test_active_medication_is_reassessed_after_profile_change_without_rewriting_history(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "female", "not_pregnant")
        medication = self.app.add_medication(person["id"], product_code="P-B", prescription_days=7)
        historical = self.app.get_medication(medication["id"])["assessment"]
        self.assertNotIn(
            "pregnancy_contraindication",
            {risk["type"] for risk in historical["risks"]},
        )
        before_change = self.app.list_medications(person["id"], as_of=date(2026, 8, 11))[0]
        self.assertFalse(before_change["dur_alert"])

        self.app.update_person(
            person["id"], "Adult", "1990-01-01", "female", "pregnant", "unknown"
        )
        current = self.app.list_medications(person["id"], as_of=date(2026, 8, 11))[0]

        self.assertTrue(current["dur_alert"])
        self.assertIn(
            "pregnancy_contraindication",
            {risk["type"] for risk in current["current_assessment"]["risks"]},
        )
        preserved = self.app.get_medication(medication["id"])["assessment"]
        self.assertEqual(preserved, historical)

    def test_acknowledged_dur_findings_remain_flagged_for_all_active_medications(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "female", "not_pregnant")
        first = self.app.add_medication(person["id"], product_code="P-A")
        second_preview = self.app.preview_medication(person["id"], "P-B")
        second = self.app.add_medication(
            person["id"],
            product_code="P-B",
            acknowledge_warnings=True,
            warning_token=second_preview["warning_token"],
        )

        listed = {item["id"]: item for item in self.app.list_medications(person["id"])}

        self.assertTrue(listed[first["id"]]["dur_alert"])
        self.assertTrue(listed[second["id"]]["dur_alert"])
        self.assertTrue(self.app.get_medication(second["id"])["assessment"]["acknowledged"])
        for medication_id in (first["id"], second["id"]):
            self.assertIn(
                "combination_contraindication",
                {risk["type"] for risk in listed[medication_id]["current_assessment"]["risks"]},
            )

    def test_exceeded_quantitative_dur_limit_keeps_persistent_alert(self) -> None:
        person = self.app.create_person("Adult", "1990-01-01", "male", "not_applicable")
        preview = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "prescription_days": 29},
        )
        medication = self.app.add_medication(
            person["id"],
            product_code="P-B",
            prescription_days=29,
            acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )

        current = self.app.list_medications(person["id"], as_of=date(2026, 8, 11))[0]

        self.assertEqual(current["id"], medication["id"])
        self.assertTrue(current["dur_alert"])
        self.assertEqual(current["current_assessment"]["duration"]["result"], "exceeded")

    def test_detects_therapeutic_duplication_and_elderly_caution(self) -> None:
        older = self.app.create_person("Older", "1940-02-01", "female", "not_pregnant")
        self.app.add_medication(older["id"], product_code="P-A")

        duplicate = self.app.preview_medication(older["id"], "P-C", as_of=date(2026, 8, 9))
        self.assertIn("therapeutic_duplication_caution", {r["type"] for r in duplicate["risks"]})

        elderly = self.app.preview_medication(older["id"], "P-D", as_of=date(2026, 8, 9))
        self.assertIn("elderly_caution", {r["type"] for r in elderly["risks"]})

    def test_records_dose_history(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        med = self.app.add_medication(
            person["id"], product_code="P-A", start_date="2026-08-09",
            schedule_times=["08:00", "20:00"],
        )

        plan = self.app.get_daily_plan(person["id"], "2026-08-09")
        completed = self.app.record_dose_instance(
            plan["doses"][0]["id"], "taken", "2026-08-09T08:03:00+09:00"
        )
        history = self.app.list_dose_logs(person["id"])

        self.assertEqual(completed["status"], "taken")
        self.assertEqual(history[0]["medication_id"], med["id"])
        self.assertEqual(history[0]["dose_instance_id"], plan["doses"][0]["id"])

    def test_default_dose_timestamp_uses_korea_timezone(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        self.app.add_medication(person["id"], product_code="P-A", schedule_times=["08:00"])
        plan = self.app.get_daily_plan(person["id"])

        self.app.record_dose_instance(plan["doses"][0]["id"], "taken")
        log = self.app.list_dose_logs(person["id"])[0]

        self.assertTrue(log["occurred_at"].endswith("+09:00"))

    def test_schedule_independent_dose_logging_is_not_exposed(self) -> None:
        self.assertFalse(hasattr(self.app, "record_dose"))

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
        self.assertEqual(results[0]["permit_status"], "active")

        unmatched = self.app.search_products("비급여전체약X", limit=10)[0]
        self.assertEqual(unmatched["product_ref"], "MFDS-X")
        self.assertIsNone(unmatched["product_code"])
        self.assertFalse(unmatched["dur_match"])

    def test_product_search_excludes_inactive_by_default_and_can_include_it(self) -> None:
        self.assertEqual(self.app.search_products("과거취하약", limit=10), [])

        results = self.app.search_products("과거취하약", limit=10, include_inactive=True)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["product_ref"], "MFDS-W")
        self.assertEqual(results[0]["permit_status"], "withdrawn")
        self.assertEqual(results[0]["permit_status_name"], "취하")
        self.assertEqual(results[0]["cancel_date"], "2025-07-01")

    def test_search_does_not_fall_back_to_dur_catalog(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-ONLY-DUR", "DUR에만있는약", "ING-ONLY-DUR", "only-dur"),
        )
        con.commit()
        con.close()

        self.assertEqual(self.app.search_products("DUR에만있는약", limit=10), [])

    def test_search_requires_full_catalog_database(self) -> None:
        missing = self.catalog_db.with_name("missing-catalog.sqlite")
        app = MedicationApp(self.dur_db, self.personal_db.with_name("other-personal.sqlite"), missing)

        with self.assertRaisesRegex(FileNotFoundError, "catalog database not available"):
            app.search_products("약A", limit=10)

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
        self.assertEqual(refreshed["summary"]["skipped"], 0)

        canceled = self.app.cancel_dose_instance(first["doses"][0]["id"])
        restored = self.app.get_daily_plan(person["id"], "2026-08-10")
        self.assertEqual(canceled["status"], "planned")
        self.assertIsNone(canceled["completed_at"])
        self.assertEqual(restored["doses"][0]["status"], "planned")
        self.assertEqual(self.app.list_dose_logs(person["id"]), [])

        canceled_again = self.app.cancel_dose_instance(first["doses"][0]["id"])
        self.assertEqual(canceled_again["status"], "planned")
        self.assertEqual(self.app.list_dose_logs(person["id"]), [])
        self.assertEqual(self.app.get_daily_plan(person["id"], "2026-08-13")["doses"], [])

    def test_medications_and_daily_doses_are_sorted_by_time_with_course_progress(self) -> None:
        person = self.app.create_person("시간순", "1990-01-01", "female", "not_pregnant")
        late = self.app.add_medication(
            person["id"], product_code="P-D", start_date="2026-08-10",
            prescription_days=5, schedule_times=["20:00"],
        )
        early = self.app.add_medication(
            person["id"], product_code="P-A", start_date="2026-08-10",
            prescription_days=5, schedule_times=["08:00"],
        )
        floating = self.app.add_medication(
            person["id"], product_code="P-D", start_date="2026-08-10",
            frequency_per_day=1,
        )
        con = sqlite3.connect(self.personal_db)
        con.execute(
            "UPDATE medication_schedules SET time_of_day='8:00' WHERE medication_id=?",
            (early["id"],),
        )
        con.commit()
        con.close()

        medications = self.app.list_medications(person["id"], as_of=date(2026, 8, 11))
        plan = self.app.get_daily_plan(person["id"], "2026-08-11")

        self.assertEqual([item["id"] for item in medications], [early["id"], late["id"], floating["id"]])
        self.assertEqual(
            medications[0]["course_progress"],
            {
                "status": "active", "total_days": 5, "current_day": 2,
                "remaining_days": 3, "progress_percent": 40,
            },
        )
        self.assertIsNone(medications[2]["course_progress"])
        self.assertEqual([dose["scheduled_time"] for dose in plan["doses"]], ["8:00", "20:00", None])

        upcoming = self.app.list_medications(person["id"], as_of=date(2026, 8, 9))[0]["course_progress"]
        first_day = self.app.list_medications(person["id"], as_of=date(2026, 8, 10))[0]["course_progress"]
        final_day = self.app.list_medications(person["id"], as_of=date(2026, 8, 14))[0]["course_progress"]
        completed = self.app.list_medications(person["id"], as_of=date(2026, 8, 15))[0]["course_progress"]
        self.assertEqual((upcoming["status"], upcoming["current_day"], upcoming["remaining_days"]), ("upcoming", 0, 5))
        self.assertEqual((first_day["current_day"], first_day["remaining_days"]), (1, 4))
        self.assertEqual((final_day["current_day"], final_day["remaining_days"]), (5, 0))
        self.assertEqual((completed["status"], completed["current_day"], completed["remaining_days"]), ("completed", 5, 0))

    def test_daily_plan_uses_unscheduled_frequency_slots_and_separates_prn(self) -> None:
        person = self.app.create_person("A", "1990-01-01", "female", "not_pregnant")
        self.app.add_medication(person["id"], product_code="P-A", frequency_per_day=3, start_date="2026-08-10")
        prn_preview = self.app.preview_medication(
            person["id"],
            {"product_code": "P-B", "as_needed": True, "dose_amount": 1, "dose_unit": "정", "start_date": "2026-08-10"},
        )
        prn = self.app.add_medication(
            person["id"], product_code="P-B", as_needed=True, dose_amount=1, dose_unit="정", start_date="2026-08-10",
            acknowledge_warnings=True, warning_token=prn_preview["warning_token"],
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
