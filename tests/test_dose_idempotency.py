from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from medicine_app.core import IdempotencyConflict, MedicationApp
from tests.test_app_core import make_canonical_db


class DoseMutationIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        canonical = root / "canonical.sqlite"
        make_canonical_db(canonical)
        self.app = MedicationApp(canonical, root / "personal.sqlite")
        self.person = self.app.create_person(
            "Dose",
            "1990-01-01",
            "male",
            "not_applicable",
            "not_applicable",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_explicit_null_note_clears_existing_note_on_same_state(self) -> None:
        self.app.add_medication(
            self.person["id"],
            product_ref="MFDS-A",
            schedule_times=["08:00"],
            frequency_per_day=1,
            start_date="2026-08-20",
            long_term=True,
        )
        instance_id = self.app.get_daily_plan(self.person["id"], "2026-08-20")["doses"][0]["id"]
        self.app.record_dose_instance(
            instance_id,
            "taken",
            "2026-08-20T08:05:00+09:00",
            "memo",
        )

        self.app.record_dose_instance(instance_id, "taken", note=None)

        log = self.app.list_dose_logs(self.person["id"])[0]
        self.assertIsNone(log["note"])

    def test_prn_request_id_is_exactly_once_and_rejects_payload_reuse(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            product_ref="MFDS-A",
            as_needed=True,
            prn_max_per_day=3,
            start_date="2026-08-20",
            long_term=True,
        )

        first = self.app.record_prn_dose(
            medication["id"],
            occurred_at="2026-08-20T12:00:00+09:00",
            note="headache",
            request_id="prn-request-1",
        )
        repeated = self.app.record_prn_dose(
            medication["id"],
            occurred_at="2026-08-20T12:00:00+09:00",
            note="headache",
            request_id="prn-request-1",
        )

        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(len(self.app.list_dose_logs(self.person["id"])), 1)
        with self.assertRaises(IdempotencyConflict):
            self.app.record_prn_dose(
                medication["id"],
                occurred_at="2026-08-20T13:00:00+09:00",
                note="different",
                request_id="prn-request-1",
            )

        self.app.cancel_dose_instance(first["id"])
        with self.assertRaises(IdempotencyConflict):
            self.app.record_prn_dose(
                medication["id"],
                occurred_at="2026-08-20T12:00:00+09:00",
                note="headache",
                request_id="prn-request-1",
            )

    def test_scheduled_record_and_cancel_are_serialized_across_callers(self) -> None:
        self.app.add_medication(
            self.person["id"],
            product_ref="MFDS-A",
            schedule_times=["08:00"],
            frequency_per_day=1,
            start_date="2026-08-20",
            long_term=True,
        )
        instance_id = self.app.get_daily_plan(self.person["id"], "2026-08-20")["doses"][0]["id"]
        self.app.record_dose_instance(
            instance_id,
            "taken",
            "2026-08-20T08:01:00+09:00",
        )
        competing = MedicationApp(self.app.canonical_db, self.app.personal_db)
        selected_existing_log = threading.Event()
        release_recorder = threading.Event()
        cancel_write_boundary = threading.Event()
        cancel_delete_attempted = threading.Event()
        recorder_errors: list[BaseException] = []
        cancel_errors: list[BaseException] = []
        real_connect = sqlite3.connect

        class HookedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                normalized = " ".join(str(sql).split())
                if threading.current_thread().name == "dose-canceler":
                    if normalized == "BEGIN IMMEDIATE":
                        cancel_write_boundary.set()
                    elif normalized.startswith("DELETE FROM dose_logs"):
                        cancel_delete_attempted.set()
                        cancel_write_boundary.set()
                cursor = super().execute(sql, parameters)
                if (
                    threading.current_thread().name == "dose-recorder"
                    and normalized.startswith("SELECT id,status,occurred_at,note FROM dose_logs")
                    and not selected_existing_log.is_set()
                ):
                    selected_existing_log.set()
                    if not release_recorder.wait(5):
                        raise TimeoutError("test recorder was not released")
                return cursor

        def hooked_connect(*args, **kwargs):
            if threading.current_thread().name in {"dose-recorder", "dose-canceler"}:
                kwargs["factory"] = HookedConnection
            return real_connect(*args, **kwargs)

        def record_skipped() -> None:
            try:
                self.app.record_dose_instance(
                    instance_id,
                    "skipped",
                    "2026-08-20T08:02:00+09:00",
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                recorder_errors.append(exc)

        def cancel_completion() -> None:
            try:
                competing.cancel_dose_instance(instance_id)
            except BaseException as exc:  # pragma: no cover - surfaced below
                cancel_errors.append(exc)

        with patch("medicine_app.core.sqlite3.connect", side_effect=hooked_connect):
            recorder = threading.Thread(target=record_skipped, name="dose-recorder")
            recorder.start()
            self.assertTrue(selected_existing_log.wait(5))
            canceler = threading.Thread(target=cancel_completion, name="dose-canceler")
            canceler.start()
            self.assertTrue(cancel_write_boundary.wait(5))
            cancel_reached_delete_before_release = cancel_delete_attempted.is_set()
            release_recorder.set()
            recorder.join(5)
            canceler.join(5)

        self.assertFalse(cancel_reached_delete_before_release)
        self.assertFalse(recorder.is_alive())
        self.assertFalse(canceler.is_alive())
        self.assertEqual(recorder_errors, [])
        self.assertEqual(cancel_errors, [])
        plan = self.app.get_daily_plan(self.person["id"], "2026-08-20")
        logs = self.app.list_dose_logs(self.person["id"])
        self.assertEqual(plan["doses"][0]["status"], "planned")
        self.assertEqual(logs, [])

    def test_prn_cancel_exact_retry_uses_canceled_tombstone(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            product_ref="MFDS-A",
            as_needed=True,
            start_date="2026-08-20",
            long_term=True,
        )
        recorded = self.app.record_prn_dose(
            medication["id"],
            occurred_at="2026-08-20T12:00:00+09:00",
            request_id="prn-cancel-retry",
        )

        first = self.app.cancel_dose_instance(recorded["id"])
        repeated = self.app.cancel_dose_instance(recorded["id"])

        self.assertTrue(first["deleted"])
        self.assertEqual(repeated["id"], recorded["id"])
        self.assertEqual(repeated["medication_id"], medication["id"])
        self.assertEqual(repeated["person_id"], self.person["id"])
        self.assertEqual(repeated["status"], "canceled")
        self.assertTrue(repeated["deleted"])
        with self.assertRaises(KeyError):
            self.app.cancel_dose_instance("never-existed")

    def test_person_deletion_erases_prn_request_tombstones(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            product_ref="MFDS-A",
            as_needed=True,
            start_date="2026-08-20",
            long_term=True,
        )
        recorded = self.app.record_prn_dose(
            medication["id"],
            occurred_at="2026-08-20T12:00:00+09:00",
            request_id="reusable-after-erasure",
        )
        self.app.cancel_dose_instance(recorded["id"])

        self.app.delete_person(self.person["id"])
        replacement = self.app.create_person(
            "Replacement",
            "1990-01-01",
            "male",
            "not_applicable",
            "not_applicable",
        )
        replacement_medication = self.app.add_medication(
            replacement["id"],
            product_ref="MFDS-A",
            as_needed=True,
            start_date="2026-08-20",
            long_term=True,
        )

        replay = self.app.record_prn_dose(
            replacement_medication["id"],
            occurred_at="2026-08-20T13:00:00+09:00",
            request_id="reusable-after-erasure",
        )
        self.assertEqual(replay["status"], "taken")


if __name__ == "__main__":
    unittest.main()