from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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