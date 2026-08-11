from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from medicine_app.cli import main
from medicine_app.core import MedicationApp
from tests.test_prescription_safety import make_catalog_db, make_dur_db


class PrescriptionCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        app = MedicationApp(self.dur_db, self.personal_db, self.catalog_db)
        self.person = app.create_person("CLI", "1990-01-01")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, object]:
        stdout = io.StringIO()
        base = [
            "--dur-db", str(self.dur_db), "--personal-db", str(self.personal_db),
            "--catalog-db", str(self.catalog_db),
        ]
        with redirect_stdout(stdout):
            result = main([*base, *arguments, "--json"])
        return result, json.loads(stdout.getvalue())

    def test_warning_token_create_update_and_history_flow(self) -> None:
        request = [
            "med-add", "--person", self.person["id"], "--product-ref", "MFDS-SAFE",
            "--dose-amount", "11", "--dose-unit", "mg", "--frequency", "1",
            "--days", "35", "--time", "08:00", "--request-id", "cli-create-1",
        ]
        status, warning = self.run_cli(*request)
        self.assertEqual(status, 2)
        self.assertTrue(warning["confirmation_required"])

        status, medication = self.run_cli(
            *request, "--acknowledge-warnings", "--warning-token", warning["warning_token"]
        )
        self.assertEqual(status, 0)
        self.assertEqual(medication["revision"], 1)

        status, updated = self.run_cli(
            "med-update", "--medication", medication["id"], "--expected-revision", "1",
            "--time", "09:00",
        )
        self.assertEqual(status, 2)
        status, updated = self.run_cli(
            "med-update", "--medication", medication["id"], "--expected-revision", "1",
            "--time", "09:00", "--acknowledge-warnings", "--warning-token", updated["warning_token"],
        )
        self.assertEqual(status, 0)
        self.assertEqual(updated["revision"], 2)

        status, history = self.run_cli("med-history", "--medication", medication["id"])
        self.assertEqual(status, 0)
        self.assertEqual([entry["revision"] for entry in history], [1, 2])

    def test_profile_update_lactation_and_delete_flow(self) -> None:
        status, created = self.run_cli(
            "person-add", "--name", "관리", "--birth-date", "1990-01-01",
            "--sex", "female", "--pregnancy-status", "not_pregnant",
            "--lactation-status", "unknown",
        )
        self.assertEqual(status, 0)

        status, updated = self.run_cli(
            "person-update", "--person", created["id"], "--name", "관리",
            "--birth-date", "1990-01-01", "--sex", "female",
            "--pregnancy-status", "not_pregnant", "--lactation-status", "breastfeeding",
        )
        self.assertEqual(status, 0)
        self.assertEqual(updated["lactation_status"], "breastfeeding")

        status, deleted = self.run_cli("person-delete", "--person", created["id"])
        self.assertEqual(status, 0)
        self.assertTrue(deleted["deleted"])

    def test_dose_instance_completion_can_be_canceled(self) -> None:
        app = MedicationApp(self.dur_db, self.personal_db, self.catalog_db)
        app.add_medication(
            self.person["id"], product_ref="MFDS-SAFE", frequency_per_day=1,
            start_date="2026-08-10", schedule_times=["08:00"],
        )
        instance = app.get_daily_plan(self.person["id"], "2026-08-10")["doses"][0]

        status, completed = self.run_cli(
            "dose-instance", "--instance", instance["id"], "--status", "taken",
            "--at", "2026-08-10T08:05:00+09:00",
        )
        self.assertEqual(status, 0)
        self.assertEqual(completed["status"], "taken")

        status, canceled = self.run_cli("dose-instance-cancel", "--instance", instance["id"])
        self.assertEqual(status, 0)
        self.assertEqual(canceled["status"], "planned")
        self.assertIsNone(canceled["completed_at"])


if __name__ == "__main__":
    unittest.main()
