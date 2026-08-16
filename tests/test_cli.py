from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import medicine_app.cli as cli_module
from medicine_app.cli import build_parser, main
from medicine_app.core import MedicationApp
from tests.test_prescription_safety import make_canonical_db


class PrescriptionCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        app = MedicationApp(self.canonical_db, self.personal_db)
        self.person = app.create_person("CLI", "1990-01-01", "male", "not_applicable", "not_applicable")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *arguments: str) -> tuple[int, object]:
        stdout = io.StringIO()
        base = [
            "--canonical-db", str(self.canonical_db), "--personal-db", str(self.personal_db),
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
            "--lactation-status", "not_breastfeeding",
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
        app = MedicationApp(self.canonical_db, self.personal_db)
        app.add_medication(
            self.person["id"], product_ref="MFDS-SAFE", dose_amount=5, dose_unit="mg",
            frequency_per_day=1, prescription_days=5, start_date="2026-08-10",
            schedule_times=["08:00"],
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

    def test_meds_exposes_date_relative_course_progress(self) -> None:
        app = MedicationApp(self.canonical_db, self.personal_db)
        app.add_medication(
            self.person["id"], product_ref="MFDS-SAFE", dose_amount=5, dose_unit="mg",
            frequency_per_day=1, start_date="2026-08-10", prescription_days=5,
            schedule_times=["08:00"],
        )

        status, medications = self.run_cli(
            "meds", "--person", self.person["id"], "--date", "2026-08-11"
        )

        self.assertEqual(status, 0)
        self.assertEqual(medications[0]["course_progress"]["remaining_days"], 3)


    def test_generic_dose_log_command_is_not_exposed(self) -> None:
        parser = build_parser()
        command_action = next(action for action in parser._actions if action.dest == "command")
        self.assertNotIn("dose-log", command_action.choices)

    def test_screenshot_can_target_the_medication_screen(self) -> None:
        args = build_parser().parse_args(["screenshot", "--screen", "meds"])
        self.assertEqual(args.screen, "meds")

    def test_screenshot_command_does_not_initialize_the_source_personal_database(self) -> None:
        payload = {"path": "/tmp/screenshot.png", "width": 390, "height": 844, "screen": "home", "size_bytes": 1}
        stdout = io.StringIO()
        arguments = [
            "--canonical-db", str(self.canonical_db),
            "--personal-db", str(self.personal_db),
            "screenshot", "--json",
        ]

        with (
            patch("medicine_app.cli.MedicationApp", side_effect=AssertionError("source DB must stay untouched")),
            patch("medicine_app.cli.capture_screenshot", return_value=payload) as screenshot,
            redirect_stdout(stdout),
        ):
            status = main(arguments)

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        screenshot.assert_called_once()

    def test_personal_database_snapshot_reads_a_read_only_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "snapshot.sqlite"
            self.personal_db.chmod(0o444)
            try:
                cli_module._snapshot_personal_database(self.personal_db, destination)
            finally:
                self.personal_db.chmod(0o644)

            with sqlite3.connect(destination) as con:
                person = con.execute("SELECT name FROM people WHERE id = ?", (self.person["id"],)).fetchone()

        self.assertEqual(person, ("CLI",))


if __name__ == "__main__":
    unittest.main()
