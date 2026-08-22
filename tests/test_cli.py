from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import medicine_app.cli as cli_module
from medicine_app.cli import build_parser, capture_screenshot, main


class _UrlResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Server:
    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class CliAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.canonical_db.touch()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *arguments: str, responses: list[dict]) -> tuple[int, object, list[list[str]]]:
        stdout = io.StringIO()
        calls: list[list[str]] = []
        queued = list(responses)

        def run_native(command, **kwargs):
            calls.append(list(command))
            payload = queued.pop(0)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        base = [
            "--canonical-db", str(self.canonical_db),
            "--personal-db", str(self.personal_db),
        ]
        with patch("medicine_app.cli.subprocess.run", side_effect=run_native), redirect_stdout(stdout):
            status = main([*base, *arguments, "--json"])
        self.assertEqual(queued, [])
        return status, json.loads(stdout.getvalue()), calls

    def test_people_command_initializes_schema_then_delegates_to_medicine_core_request(self) -> None:
        status, payload, calls = self.run_cli(
            "people",
            responses=[
                {"status": 200, "body": {"initialized": True, "schema_version": 4}},
                {"status": 200, "body": [{"id": "person-1", "name": "Rust"}]},
            ],
        )

        self.assertEqual(status, 0)
        self.assertEqual(payload, [{"id": "person-1", "name": "Rust"}])
        self.assertEqual(calls[0][1:3], ["personal-schema", "--personal-db"])
        self.assertIn(str(self.personal_db), calls[0])
        self.assertEqual(calls[1][1:4], ["request", "GET", "/api/people"])
        self.assertIn("--canonical-db", calls[1])
        self.assertIn("--personal-db", calls[1])
        self.assertEqual(calls[1][-1], "--json")

    def test_confirmation_and_product_search_keep_existing_cli_exit_contracts(self) -> None:
        confirmation = {
            "status": 409,
            "body": {
                "confirmation_required": True,
                "request_id": "create-1",
                "warning_token": "warning-token",
                "assessment": {"warning_token": "warning-token"},
            },
        }
        status, payload, calls = self.run_cli(
            "med-add", "--person", "person-1", "--product-ref", "MFDS-X",
            "--long-term", "--request-id", "create-1",
            responses=[
                {"status": 200, "body": {"initialized": True, "schema_version": 4}},
                confirmation,
            ],
        )
        self.assertEqual(status, 2)
        self.assertTrue(payload["confirmation_required"])
        self.assertEqual(calls[1][1:3], ["request", "POST"])
        body = json.loads(calls[1][calls[1].index("--body") + 1])
        self.assertEqual(body["product_ref"], "MFDS-X")
        self.assertTrue(body["long_term"])

        status, payload, calls = self.run_cli(
            "drug-search", "씬지록신 25", "--explain-matches",
            responses=[
                {"status": 200, "body": {"initialized": True, "schema_version": 4}},
                {"status": 503, "body": {"detail": "product search engine is not implemented"}},
            ],
        )
        self.assertEqual(status, 3)
        self.assertIn("not implemented", payload["detail"])
        self.assertIn("/api/products?", calls[1][3])

    def test_runtime_error_is_structured_instead_of_falling_back_to_python_domain(self) -> None:
        status, payload, _ = self.run_cli(
            "prn-intake", "--medication", "m1", "--request-id", "reuse",
            responses=[
                {"status": 200, "body": {"initialized": True, "schema_version": 4}},
                {"status": 409, "body": {"detail": "request_id was already used with different input"}},
            ],
        )
        self.assertEqual(status, 1)
        self.assertIn("different input", payload["detail"])

    def test_meds_uses_rust_dashboard_and_presents_only_medications(self) -> None:
        status, payload, calls = self.run_cli(
            "meds", "--person", "person-1", "--date", "2026-08-11",
            responses=[
                {"status": 200, "body": {"initialized": True, "schema_version": 4}},
                {"status": 200, "body": {"medications": [{"id": "m1"}], "daily_plan": {}}},
            ],
        )
        self.assertEqual(status, 0)
        self.assertEqual(payload, [{"id": "m1"}])
        self.assertIn("/api/people/person-1/dashboard?date=2026-08-11", calls[1])

    def test_screenshot_can_target_the_medication_screen(self) -> None:
        args = build_parser().parse_args(["screenshot", "--screen", "meds"])
        self.assertEqual(args.screen, "meds")

    def test_screenshot_does_not_offer_removed_settings_screen(self) -> None:
        parser = build_parser()
        screen_action = next(
            action
            for action in parser._subparsers._group_actions[0].choices["screenshot"]._actions
            if action.dest == "screen"
        )
        self.assertNotIn("settings", screen_action.choices)

    def test_screenshot_launches_rust_web_against_read_only_snapshot(self) -> None:
        with sqlite3.connect(self.personal_db) as con:
            con.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            con.execute("INSERT INTO marker(value) VALUES ('source')")
        output = Path(self.tmp.name) / "shot.png"
        popen_commands: list[list[str]] = []

        def popen(command, **kwargs):
            popen_commands.append(list(command))
            return _Server()

        def browser_run(command, **kwargs):
            screenshot_arg = next(value for value in command if value.startswith("--screenshot="))
            Path(screenshot_arg.split("=", 1)[1]).write_bytes(b"png")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("medicine_app.cli.shutil.which", return_value="/usr/bin/chromium"),
            patch("medicine_app.cli.subprocess.Popen", side_effect=popen),
            patch("medicine_app.cli.subprocess.run", side_effect=browser_run),
            patch("medicine_app.cli.urllib.request.urlopen", return_value=_UrlResponse()) as urlopen,
        ):
            result = capture_screenshot(
                self.canonical_db,
                self.personal_db,
                output,
                390,
                844,
                "meds",
            )

        self.assertEqual(result["size_bytes"], 3)
        self.assertEqual(popen_commands[0][0], os.environ.get("MEDICINE_CORE_WEB_BINARY", "medicine-core-web"))
        self.assertIn("--canonical-db", popen_commands[0])
        self.assertIn("--personal-db", popen_commands[0])
        self.assertIn("--static-dir", popen_commands[0])
        health_url = urlopen.call_args.args[0]
        self.assertTrue(health_url.endswith("/api/health"), health_url)
        self.assertNotIn("screen=", health_url)

    def test_personal_database_snapshot_reads_a_read_only_source(self) -> None:
        with sqlite3.connect(self.personal_db) as con:
            con.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            con.execute("INSERT INTO marker(value) VALUES ('source')")
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "snapshot.sqlite"
            self.personal_db.chmod(0o444)
            try:
                cli_module._snapshot_personal_database(self.personal_db, destination)
            finally:
                self.personal_db.chmod(0o644)

            with sqlite3.connect(destination) as con:
                marker = con.execute("SELECT value FROM marker").fetchone()
        self.assertEqual(marker, ("source",))


if __name__ == "__main__":
    unittest.main()
