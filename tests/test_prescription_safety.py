from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

from medicine_app.core import ConfirmationRequired, MedicationApp


APP_TZ = ZoneInfo("Asia/Seoul")


def make_canonical_db(path: Path) -> None:
    from tests.canonical_fixture_support import create_canonical_fixture, add_product, add_linked_rule
    con = create_canonical_fixture(path)
    add_product(con, "MFDS-SAFE", "정량비교약", "example", manufacturer="예시제약", dosage_form="정제", edi="P-SAFE")
    add_product(con, "MFDS-UNKNOWN", "비교불가약", "unknown", manufacturer="예시제약", dosage_form="정제", edi="P-UNKNOWN")
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-SAFE", ingredient="example",
        rule_value="28", details="최대 투여기간은 28일입니다.", dosage_form="정제",
    )
    add_linked_rule(
        con, category="dose_caution", item_seq="MFDS-SAFE", ingredient="example",
        rule_value="예시성분 10mg",
        details=None,
        dosage_form="정제",
    )
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-UNKNOWN", ingredient="unknown",
        rule_value="복용기간 확인 필요", details="수치화할 수 없는 기준입니다.", dosage_form="정제",
    )
    add_linked_rule(
        con, category="dose_caution", item_seq="MFDS-UNKNOWN", ingredient="unknown",
        rule_value="적합 단위 확인 필요", details="수치화할 수 없는 기준입니다.", dosage_form="정제",
    )
    con.commit()
    con.close()


class PrescriptionSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        self.app = MedicationApp(self.canonical_db, self.personal_db)
        self.person = self.app.create_person("환자", "1990-01-01", "unknown", "unknown")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _draft(**overrides: object) -> dict:
        draft = {
            "product_ref": "MFDS-SAFE",
            "dose_amount": 5,
            "dose_unit": "mg",
            "frequency_per_day": 1,
            "prescription_days": 7,
            "start_date": "2026-08-10",
            "schedule_times": ["08:00"],
        }
        draft.update(overrides)
        return draft

    @staticmethod
    def _dimension(preview: dict, name: str) -> dict:
        return preview["quantitative_checks"][name]

    def _set_duration_rule(self, rule_value: str) -> None:
        con = sqlite3.connect(self.canonical_db)
        con.execute(
            """UPDATE ingredient_rules SET rule_value=?
               WHERE id IN (
                   SELECT criterion_rule_id FROM product_criterion_links l
                   JOIN product_rules r ON r.id=l.product_rule_id
                   WHERE r.item_seq='MFDS-SAFE' AND r.category='duration_caution'
               )""",
            (rule_value,),
        )
        con.commit()
        con.close()

    def test_duration_week_rule_is_converted_to_days(self) -> None:
        self._set_duration_rule("1주")

        within = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=7)
        )
        exceeded = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=8)
        )

        self.assertEqual(self._dimension(within, "duration")["result"], "within")
        self.assertEqual(self._dimension(within, "duration")["maximum_days"], 7)
        self.assertEqual(self._dimension(exceeded, "duration")["result"], "exceeded")

    def test_duration_month_rule_fails_closed_instead_of_treating_months_as_days(self) -> None:
        self._set_duration_rule("6개월")

        preview = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=180)
        )

        duration = self._dimension(preview, "duration")
        self.assertEqual(duration["result"], "not_evaluable")
        self.assertIn("month", duration["reason"])
        self.assertNotIn("maximum_days", duration)

    def test_full_preview_reports_duration_exceeded_and_dose_within(self) -> None:
        con = sqlite3.connect(self.canonical_db)
        con.execute(
            "UPDATE product_rules SET details=NULL WHERE item_seq='MFDS-SAFE' AND category='duration_caution'"
        )
        con.commit()
        con.close()
        preview = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=35, dose_amount=5)
        )

        self.assertEqual(self._dimension(preview, "duration")["result"], "exceeded")
        self.assertEqual(self._dimension(preview, "dose")["result"], "within")
        quantitative_risks = [
            risk for risk in preview["risks"]
            if risk["type"] in {"duration_caution", "dose_caution"}
        ]
        self.assertEqual(quantitative_risks, [])
        statuses = {item["category"]: item for item in preview["dur_checks"]}
        self.assertEqual(statuses["duration_caution"]["status"], "hit")
        self.assertEqual(statuses["duration_caution"]["summary"], "투여기간주의 기준 초과")
        self.assertEqual(
            statuses["duration_caution"]["details"],
            "입력한 투여기간 35일 · DUR 기준 28일",
        )
        self.assertEqual(statuses["dose_caution"]["status"], "clear")

    def test_full_preview_reports_duration_within_and_dose_exceeded(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=7, dose_amount=11, frequency_per_day=1)
        )

        self.assertEqual(self._dimension(preview, "duration")["result"], "within")
        self.assertEqual(self._dimension(preview, "dose")["result"], "exceeded")
        statuses = {item["category"]: item for item in preview["dur_checks"]}
        self.assertEqual(statuses["dose_caution"]["status"], "hit")
        self.assertEqual(statuses["dose_caution"]["summary"], "용량주의 기준 초과")
        self.assertEqual(
            statuses["dose_caution"]["details"],
            "입력한 1일 용량 11.0mg · DUR 기준 10.0mg",
        )

    def test_duplicate_links_with_same_materialized_threshold_remain_evaluable(self) -> None:
        from tests.canonical_fixture_support import add_linked_rule

        con = sqlite3.connect(self.canonical_db)
        add_linked_rule(
            con, category="dose_caution", item_seq="MFDS-SAFE", ingredient="example-duplicate",
            rule_value="같은기준 10mg", dosage_form="정제",
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"], self._draft(dose_amount=5, frequency_per_day=1)
        )
        self.assertEqual(self._dimension(preview, "dose")["result"], "within")
        self.assertEqual(self._dimension(preview, "dose")["maximum_daily_amount"], 10.0)

    def test_duplicate_links_with_conflicting_materialized_thresholds_fail_closed(self) -> None:
        from tests.canonical_fixture_support import add_linked_rule

        con = sqlite3.connect(self.canonical_db)
        add_linked_rule(
            con, category="dose_caution", item_seq="MFDS-SAFE", ingredient="example-conflict",
            rule_value="다른기준 20mg", dosage_form="정제",
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"], self._draft(dose_amount=5, frequency_per_day=1)
        )
        dose = self._dimension(preview, "dose")
        self.assertEqual(dose["result"], "not_evaluable")
        self.assertIn("one unambiguous daily threshold", dose["reason"])

    def test_rules_within_threshold_do_not_require_a_second_registration_click(self) -> None:
        draft = self._draft(prescription_days=7, dose_amount=5)

        preview = self.app.preview_medication(self.person["id"], draft)
        medication = self.app.add_medication(
            self.person["id"], **draft, request_id="within-threshold-one-click"
        )

        self.assertIsNone(preview["warning_token"])
        self.assertFalse(medication["assessment"]["acknowledged"])

    def test_countable_dose_uses_product_rule_form_but_requires_authoritative_content(self) -> None:
        con = sqlite3.connect(self.canonical_db)
        con.execute("UPDATE products SET dosage_form=NULL WHERE item_seq='MFDS-SAFE'")
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"],
            self._draft(dose_amount=1, dose_unit="정", frequency_per_day=3, schedule_times=[]),
        )

        dose = self._dimension(preview, "dose")
        self.assertEqual(dose["result"], "not_evaluable")
        self.assertIn("per-unit ingredient content", dose["reason"])
        self.assertNotIn("dosage form", dose["reason"])

    def test_full_preview_marks_each_dimension_not_evaluable_with_reason(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            self._draft(
                product_ref="MFDS-UNKNOWN",
                prescription_days=None,
                dose_amount=1,
                dose_unit="tablet",
            ),
        )

        for name in ("duration", "dose"):
            dimension = self._dimension(preview, name)
            self.assertEqual(dimension["result"], "not_evaluable")
            self.assertTrue(dimension["reason"])

    def test_fractional_frequency_and_duration_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.app.add_medication(
                self.person["id"], **self._draft(frequency_per_day=1.5), request_id="fraction-frequency"
            )
        with self.assertRaises(ValueError):
            self.app.add_medication(
                self.person["id"], **self._draft(prescription_days=1.5), request_id="fraction-days"
            )

    def test_single_digit_schedule_hour_is_canonicalized_before_storage(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            **self._draft(schedule_times=["8:00"], frequency_per_day=1),
            request_id="canonical-time",
        )

        self.assertEqual(medication["schedules"][0]["time_of_day"], "08:00")
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.app.add_medication(
                self.person["id"],
                **self._draft(schedule_times=["8:00", "08:00"], frequency_per_day=2),
                request_id="canonical-time-duplicate",
            )

    def test_materialized_unparseable_source_threshold_is_not_evaluable(self) -> None:
        con = sqlite3.connect(self.canonical_db)
        criterion_id = con.execute(
            """SELECT criterion_rule_id FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               WHERE r.item_seq='MFDS-SAFE' AND r.category='dose_caution'"""
        ).fetchone()[0]
        con.execute(
            """UPDATE dose_criteria
               SET maximum_daily_amount=NULL,maximum_daily_unit=NULL,
                   parse_status='not_evaluable',parse_reason='unsupported source unit'
               WHERE criterion_rule_id=?""",
            (criterion_id,),
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"], self._draft(dose_amount=5, dose_unit="mg")
        )

        dose = self._dimension(preview, "dose")
        self.assertEqual(dose["result"], "not_evaluable")
        self.assertEqual(dose["reason"], "unsupported source unit")

    def test_runtime_uses_materialized_canonical_threshold_not_legacy_details(self) -> None:
        con = sqlite3.connect(self.canonical_db)
        criterion_id = con.execute(
            """SELECT criterion_rule_id FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               WHERE r.item_seq='MFDS-SAFE' AND r.category='dose_caution'"""
        ).fetchone()[0]
        con.execute(
            """UPDATE dose_criteria
               SET maximum_daily_amount='4000',maximum_daily_unit='mg',parse_status='parsed',parse_reason=NULL
               WHERE criterion_rule_id=?""",
            (criterion_id,),
        )
        con.execute(
            "UPDATE ingredient_rules SET rule_value='this source text is deliberately unparsable' WHERE id=?",
            (criterion_id,),
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(self.person["id"], self._draft(dose_amount=5))

        self.assertEqual(self._dimension(preview, "dose")["result"], "within")
        self.assertEqual(self._dimension(preview, "dose")["maximum_daily_amount"], 4000.0)

    def test_adult_dose_threshold_does_not_reassure_a_child(self) -> None:
        child = self.app.create_person("소아", "2015-01-01", "female", "not_pregnant")

        preview = self.app.preview_medication(child["id"], self._draft(dose_amount=5))

        dose = self._dimension(preview, "dose")
        self.assertEqual(dose["result"], "not_evaluable")
        self.assertIn("adult", dose["reason"])
        self.assertNotIn("coverage_only", dose)

    def test_concurrent_schema_initialization_is_serialized(self) -> None:
        concurrent_db = Path(self.tmp.name) / "concurrent.sqlite"
        con = sqlite3.connect(concurrent_db)
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
            """
        )
        con.commit()
        con.close()
        barrier = Barrier(12)

        def initialize(_: int) -> MedicationApp:
            barrier.wait()
            return MedicationApp(self.canonical_db, concurrent_db)

        with ThreadPoolExecutor(max_workers=12) as executor:
            apps = list(executor.map(initialize, range(12)))

        self.assertEqual(len(apps), 12)

    def test_exceeded_create_requires_acknowledgement(self) -> None:
        draft = self._draft(prescription_days=35, dose_amount=11)
        with self.assertRaisesRegex(ValueError, "acknowledg"):
            self.app.add_medication(
                self.person["id"],
                **draft,
                request_id="create-exceeded-1",
                acknowledge_warnings=False,
            )
        self.assertEqual(self.app.list_medications(self.person["id"]), [])

    def test_exceeded_create_succeeds_only_after_explicit_acknowledgement(self) -> None:
        draft = self._draft(prescription_days=35, dose_amount=11)
        with self.assertRaises(ConfirmationRequired) as warning:
            self.app.add_medication(
                self.person["id"], **draft, request_id="create-exceeded-2"
            )
        medication = self.app.add_medication(
            self.person["id"],
            **draft,
            request_id="create-exceeded-2",
            acknowledge_warnings=True,
            warning_token=warning.exception.assessment["warning_token"],
        )

        self.assertEqual(medication["assessment"]["duration"]["result"], "exceeded")
        self.assertEqual(medication["assessment"]["acknowledged"], True)

    def test_create_request_id_is_idempotent_and_rejects_payload_mismatch(self) -> None:
        draft = self._draft()
        kwargs = {
            **draft,
            "request_id": "create-idempotent-1",
            "acknowledge_warnings": False,
        }

        first = self.app.add_medication(self.person["id"], **kwargs)
        retry = self.app.add_medication(self.person["id"], **kwargs)
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(len(self.app.list_medications(self.person["id"])), 1)

        with self.assertRaisesRegex(ValueError, "request_id|payload"):
            self.app.add_medication(
                self.person["id"],
                **{**kwargs, "dose_amount": 6},
            )

    def test_update_requires_expected_revision_and_records_history(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            **self._draft(),
            request_id="update-history-create",
        )
        revision = medication["revision"]

        with self.assertRaises((TypeError, ValueError)):
            self.app.update_medication(medication["id"], schedule_times=["09:00"])

        updated = self.app.update_medication(
            medication["id"], expected_revision=revision, schedule_times=["09:00"], dose_amount=6
        )

        self.assertEqual(updated["revision"], revision + 1)
        history = self.app.list_medication_revisions(medication["id"])
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[-1]["revision"], updated["revision"])
        self.assertEqual(history[-1]["action"], "update")

    def test_update_replaces_schedules_and_removes_only_future_planned_instances(self) -> None:
        today = datetime.now(APP_TZ).date()
        medication = self.app.add_medication(
            self.person["id"],
            **self._draft(
                start_date=(today - timedelta(days=1)).isoformat(),
                prescription_days=4,
                frequency_per_day=2,
                schedule_times=["08:00", "20:00"],
            ),
            request_id="schedule-replacement-create",
        )
        completed_day = (today - timedelta(days=1)).isoformat()
        future_day = (today + timedelta(days=1)).isoformat()
        completed_plan = self.app.get_daily_plan(self.person["id"], completed_day)
        future_plan = self.app.get_daily_plan(self.person["id"], future_day)
        completed_instance = completed_plan["doses"][0]
        self.app.record_dose_instance(
            completed_instance["id"], "taken", f"{completed_day}T08:05:00+09:00"
        )
        old_future_ids = {dose["id"] for dose in future_plan["doses"]}

        updated = self.app.update_medication(
            medication["id"], expected_revision=medication["revision"], schedule_times=["09:00"]
        )

        refreshed = self.app.get_daily_plan(self.person["id"], future_day)
        self.assertEqual([dose["scheduled_time"] for dose in refreshed["doses"]], ["09:00"])
        self.assertTrue(old_future_ids.isdisjoint({dose["id"] for dose in refreshed["doses"]}))
        completed_refreshed = self.app.get_daily_plan(self.person["id"], completed_day)
        self.assertEqual(completed_refreshed["doses"][0]["status"], "taken")
        self.assertEqual(len(self.app.list_dose_logs(self.person["id"])), 1)
        self.assertEqual(updated["schedules"][0]["time_of_day"], "09:00")

    def test_stop_records_a_revision(self) -> None:
        medication = self.app.add_medication(
            self.person["id"], **self._draft(), request_id="stop-history-create"
        )

        stopped = self.app.stop_medication(medication["id"], expected_revision=medication["revision"])

        self.assertFalse(stopped["active"])
        self.assertEqual(stopped["revision"], medication["revision"] + 1)
        history = self.app.list_medication_revisions(medication["id"])
        self.assertEqual(history[-1]["action"], "stop")


if __name__ == "__main__":
    unittest.main()
