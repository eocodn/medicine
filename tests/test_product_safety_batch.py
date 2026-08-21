from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from medicine_app.core import ConfirmationRequired, MedicationApp
from medicine_app.safety import age_years
from tests.canonical_fixture_support import (
    add_linked_rule,
    add_product,
    add_unlinked_rule,
    create_canonical_fixture,
)


def build_reference(path: Path) -> None:
    con = create_canonical_fixture(path)
    add_product(con, "SAFE", "안전약", "safe-drug")
    add_product(con, "A", "병용약A", "drug-a")
    add_product(con, "B", "병용약B", "drug-b")
    add_product(con, "ELDER", "노인주의약", "elder-drug")
    add_product(con, "UNRES", "부분미확인약", "unresolved-drug")
    add_product(con, "PARTIAL", "부분DUR약", "partial-drug")
    add_product(
        con,
        "OLD",
        "취하약",
        "old-drug",
        permit_status="withdrawn",
        cancel_date="2025-01-01",
        cancel_name="취하",
    )
    add_linked_rule(
        con,
        category="combination_contraindication",
        item_seq="A",
        ingredient="drug-a",
        paired_item_seq="B",
        paired_ingredient="drug-b",
        details="함께 사용하지 않아야 함",
    )
    add_linked_rule(
        con,
        category="elderly_caution",
        item_seq="ELDER",
        ingredient="elder-drug",
        details="65세 이상 주의",
    )
    add_unlinked_rule(
        con,
        category="age_contraindication",
        item_seq="UNRES",
        ingredient="unresolved-drug",
        details="상세 기준 연결 실패",
    )
    add_linked_rule(
        con,
        category="elderly_caution",
        item_seq="PARTIAL",
        ingredient="partial-drug",
        details="65세 이상 주의",
    )
    add_unlinked_rule(
        con,
        category="duration_caution",
        item_seq="PARTIAL",
        ingredient="partial-drug",
        details="투여기간 상세 기준 연결 실패",
    )
    con.commit()
    con.close()


class ProductSafetyBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical = root / "canonical.sqlite"
        self.personal = root / "personal.sqlite"
        build_reference(self.canonical)
        self.app = MedicationApp(self.canonical, self.personal)
        self.person = self.app.create_person(
            "사용자", "1990-01-01", "male", "not_applicable", "not_applicable"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_safe(self, **changes):
        values = {
            "product_ref": "SAFE",
            "dose_amount": 1,
            "dose_unit": "정",
            "frequency_per_day": 2,
            "prescription_days": 3,
            "schedule_times": ["08:00", "20:00"],
            "start_date": "2026-08-15",
        }
        values.update(changes)
        return self.app.add_medication(self.person["id"], **values)

    def _set_safe_permit_status(self, status: str, cancel_name: str, cancel_date: str) -> None:
        con = sqlite3.connect(self.canonical)
        try:
            con.execute(
                "UPDATE products SET permit_status=?,cancel_name=?,cancel_date=? WHERE item_seq='SAFE'",
                (status, cancel_name, cancel_date),
            )
            con.commit()
        finally:
            con.close()

    def test_same_day_schedule_edit_does_not_create_an_extra_dose(self) -> None:
        medication = self._add_safe()
        plan = self.app.get_daily_plan(self.person["id"], "2026-08-15")
        morning = next(item for item in plan["doses"] if item["scheduled_time"] == "08:00")
        self.app.record_dose_instance(morning["id"], "taken", "2026-08-15T08:05:00+09:00")

        self.app.update_medication(
            medication["id"],
            expected_revision=medication["revision"],
            schedule_times=["09:00", "21:00"],
        )
        refreshed = self.app.get_daily_plan(self.person["id"], "2026-08-15")

        self.assertEqual(
            [(item["scheduled_time"], item["status"]) for item in refreshed["doses"]],
            [("08:00", "taken"), ("21:00", "planned")],
        )

    def test_blank_duration_requires_explicit_long_term_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "duration|long-term|long_term"):
            self.app.add_medication(
                self.person["id"], product_ref="SAFE", frequency_per_day=1,
                schedule_times=["08:00"], start_date="2026-08-15",
            )

        medication = self.app.add_medication(
            self.person["id"], product_ref="SAFE", frequency_per_day=1,
            schedule_times=["08:00"], start_date="2026-08-15", long_term=True,
        )
        self.assertTrue(medication["long_term"])
        future = self.app.get_daily_plan(self.person["id"], "2027-08-15")
        self.assertEqual(len(future["doses"]), 1)

    def test_skipped_dose_can_be_corrected_back_to_planned(self) -> None:
        self._add_safe(frequency_per_day=1, schedule_times=["08:00"])
        plan = self.app.get_daily_plan(self.person["id"], "2026-08-15")
        skipped = self.app.record_dose_instance(
            plan["doses"][0]["id"], "skipped", "2026-08-15T08:05:00+09:00"
        )

        restored = self.app.cancel_dose_instance(skipped["id"])

        self.assertEqual(restored["status"], "planned")
        self.assertIsNone(restored["completed_at"])
        self.assertEqual(self.app.list_dose_logs(self.person["id"]), [])

    def test_current_medication_exposes_unresolved_safety_badge_state(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "UNRES", "prescription_days": 3, "start_date": "2026-08-15"},
        )
        medication = self.app.add_medication(
            self.person["id"], product_ref="UNRES", prescription_days=3,
            start_date="2026-08-15", acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )

        current = next(
            item for item in self.app.list_medications(self.person["id"], as_of="2026-08-15")
            if item["id"] == medication["id"]
        )
        self.assertFalse(current["dur_alert"])
        self.assertTrue(current["dur_review_required"])

    def test_existing_medication_surfaces_later_permit_change_without_stopping_regimen(self) -> None:
        medication = self._add_safe(frequency_per_day=1, schedule_times=["08:00"])
        first_plan = self.app.get_daily_plan(self.person["id"], "2026-08-15")
        self.app.record_dose_instance(
            first_plan["doses"][0]["id"], "taken", "2026-08-15T08:05:00+09:00"
        )
        self._set_safe_permit_status("expired", "유효기간만료", "2026-08-16")

        current = next(
            item for item in self.app.list_medications(self.person["id"], as_of="2026-08-16")
            if item["id"] == medication["id"]
        )
        self.assertEqual(current["permit_status"], "expired")
        self.assertEqual(current["permit_status_name"], "유효기간만료")
        self.assertEqual(current["permit_status_changed_at"], "2026-08-16")

        second_plan = self.app.get_daily_plan(self.person["id"], "2026-08-16")
        self.assertEqual(
            [(item["scheduled_time"], item["status"]) for item in second_plan["doses"]],
            [("08:00", "planned")],
        )
        updated = self.app.update_medication(
            medication["id"], expected_revision=medication["revision"], dose_amount=2
        )
        self.assertEqual(updated["dose_amount"], 2)

        history = self.app.list_dose_logs(self.person["id"])
        self.assertEqual(len(history), 1)
        self.assertNotIn("permit_status", history[0])
        self.assertNotIn("permit_status_changed_at", history[0])

    def test_existing_prn_medication_remains_recordable_after_permit_change(self) -> None:
        medication = self._add_safe(
            as_needed=True,
            frequency_per_day=None,
            schedule_times=[],
            prn_max_per_day=2,
        )
        self._set_safe_permit_status("withdrawn", "취하", "2026-08-16")

        recorded = self.app.record_prn_dose(
            medication["id"], "2026-08-16T12:00:00+09:00"
        )

        self.assertEqual(recorded["status"], "taken")

    def test_future_course_uses_age_during_treatment(self) -> None:
        person = self.app.create_person(
            "생일전", "1961-08-20", "female", "not_pregnant", "not_breastfeeding"
        )
        preview = self.app.preview_medication(
            person["id"],
            {"product_ref": "ELDER", "start_date": "2026-08-25", "prescription_days": 7},
        )
        elderly = next(item for item in preview["dur_checks"] if item["category"] == "elderly_caution")
        self.assertEqual(elderly["status"], "hit")

    def test_stopping_future_course_removes_phantom_interaction(self) -> None:
        medication = self.app.add_medication(
            self.person["id"], product_ref="A", start_date="2026-09-01", prescription_days=3
        )
        self.app.stop_medication(medication["id"], expected_revision=medication["revision"])

        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "B", "start_date": "2026-10-01", "prescription_days": 3},
        )
        combination = next(
            item for item in preview["dur_checks"] if item["category"] == "combination_contraindication"
        )
        self.assertEqual(combination["status"], "clear")

    def test_exact_duplicate_regimen_requires_confirmation_but_can_be_added(self) -> None:
        self._add_safe()
        draft = {
            "product_ref": "SAFE", "dose_amount": 1, "dose_unit": "정",
            "frequency_per_day": 2, "prescription_days": 3,
            "schedule_times": ["08:00", "20:00"], "start_date": "2026-08-15",
        }
        preview = self.app.preview_medication(self.person["id"], draft)

        self.assertTrue(preview["warning_token"])
        self.assertEqual(preview["review_items"][0]["type"], "duplicate_regimen")
        with self.assertRaises(ConfirmationRequired):
            self.app.add_medication(self.person["id"], **draft)
        duplicate = self.app.add_medication(
            self.person["id"], **draft, acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )
        self.assertEqual(duplicate["product_name"], "안전약")

    def test_new_profiles_require_binary_sex_and_reproductive_states(self) -> None:
        with self.assertRaisesRegex(ValueError, "sex"):
            self.app.create_person("미선택", "1990-01-01", "unknown", "unknown", "unknown")
        with self.assertRaisesRegex(ValueError, "pregnancy_status"):
            self.app.create_person("여성", "1990-01-01", "female", "unknown", "not_breastfeeding")
        with self.assertRaisesRegex(ValueError, "lactation_status"):
            self.app.create_person("여성", "1990-01-01", "female", "not_pregnant", "unknown")

        female = self.app.create_person(
            "여성", "1990-01-01", "female", "not_pregnant", "not_breastfeeding"
        )
        male = self.app.create_person(
            "남성", "1990-01-01", "male", "pregnant", "breastfeeding"
        )
        self.assertFalse(female["profile_needs_review"])
        self.assertEqual(male["pregnancy_status"], "not_applicable")
        self.assertEqual(male["lactation_status"], "not_applicable")

    def test_legacy_unknown_profile_is_preserved_but_marked_for_review(self) -> None:
        con = sqlite3.connect(self.personal)
        con.execute(
            "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status) VALUES(?,?,?,?,?,?)",
            ("legacy", "기존", "1990-01-01", "unknown", "unknown", "unknown"),
        )
        con.commit()
        con.close()

        legacy = next(item for item in self.app.list_people() if item["id"] == "legacy")
        self.assertEqual(legacy["sex"], "unknown")
        self.assertTrue(legacy["profile_needs_review"])

    def test_inactive_permit_product_is_hard_blocked_from_current_regimen(self) -> None:
        with self.assertRaisesRegex(ValueError, "inactive|permit|허가"):
            self.app.add_medication(
                self.person["id"], product_ref="OLD", start_date="2026-08-15", prescription_days=7
            )

    def test_product_lookup_reports_partial_dur_coverage_without_overstatement(self) -> None:
        result = self.app.get_product("PARTIAL")
        self.assertTrue(result["dur_match"])
        self.assertEqual(result["dur_coverage_status"], "partial")

    def test_completed_finite_course_is_not_returned_as_current(self) -> None:
        self.app.add_medication(
            self.person["id"], product_ref="SAFE", start_date="2026-08-01", prescription_days=3
        )
        self.assertEqual(self.app.list_medications(self.person["id"], as_of="2026-08-15"), [])

    def test_ended_unresolved_medication_does_not_poison_future_interaction_coverage(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "UNRES", "start_date": "2026-08-01", "prescription_days": 3},
        )
        self.app.add_medication(
            self.person["id"], product_ref="UNRES", start_date="2026-08-01", prescription_days=3,
            acknowledge_warnings=True, warning_token=preview["warning_token"],
        )

        future = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "SAFE", "start_date": "2026-09-01", "prescription_days": 3},
        )
        checks = {item["category"]: item for item in future["dur_checks"]}
        self.assertEqual(checks["combination_contraindication"]["status"], "clear")
        self.assertEqual(checks["therapeutic_duplication_caution"]["status"], "clear")

    def test_prn_and_fixed_schedule_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "PRN|as_needed|schedule"):
            self.app.add_medication(
                self.person["id"], product_ref="SAFE", as_needed=True,
                schedule_times=["08:00"], frequency_per_day=1,
                start_date="2026-08-15", long_term=True,
            )

        prn = self.app.add_medication(
            self.person["id"], product_ref="SAFE", as_needed=True,
            prn_max_per_day=2, start_date="2026-08-15", long_term=True,
        )
        self.assertTrue(prn["as_needed"])
        self.assertEqual(prn["prn_max_per_day"], 2)
        self.assertEqual(prn["schedules"], [])
        self.assertIsNone(prn["frequency_per_day"])

    def test_switching_existing_schedule_to_prn_clears_fixed_occurrences(self) -> None:
        medication = self._add_safe()

        updated = self.app.update_medication(
            medication["id"], expected_revision=medication["revision"],
            as_needed=True, prn_max_per_day=2,
        )

        self.assertTrue(updated["as_needed"])
        self.assertEqual(updated["prn_max_per_day"], 2)
        self.assertIsNone(updated["frequency_per_day"])
        self.assertEqual(updated["schedules"], [])
        plan = self.app.get_daily_plan(self.person["id"], "2026-08-15")
        self.assertEqual(plan["doses"], [])
        self.assertEqual([item["id"] for item in plan["prn_medications"]], [medication["id"]])

    def test_prn_actual_intake_uses_instance_history_and_can_be_undone(self) -> None:
        prn = self.app.add_medication(
            self.person["id"], product_ref="SAFE", as_needed=True,
            prn_max_per_day=2, start_date="2026-08-15", long_term=True,
        )
        recorded = self.app.record_prn_dose(
            prn["id"], occurred_at="2026-08-15T12:30:00+09:00", note="증상 시"
        )
        self.assertEqual(recorded["status"], "taken")
        logs = self.app.list_dose_logs(self.person["id"])
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["dose_instance_id"], recorded["id"])

        self.app.record_prn_dose(
            prn["id"], occurred_at="2026-08-15T15:30:00+09:00", note="재복용"
        )
        with self.assertRaisesRegex(ValueError, "maximum"):
            self.app.record_prn_dose(
                prn["id"], occurred_at="2026-08-15T18:30:00+09:00", note="초과 시도"
            )

        restored = self.app.cancel_dose_instance(recorded["id"])
        self.assertEqual(restored["status"], "canceled")
        self.assertTrue(restored["deleted"])
        self.assertEqual(len(self.app.list_dose_logs(self.person["id"])), 1)

    def test_leap_day_age_uses_same_february_28_anniversary_as_age_rules(self) -> None:
        self.assertEqual(age_years("1960-02-29", date(2025, 2, 28)), 65)


if __name__ == "__main__":
    unittest.main()
