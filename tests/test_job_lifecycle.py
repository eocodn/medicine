from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.job_lifecycle import JobLifecycle, fingerprint_inputs, sqlite_heartbeat


class JobLifecycleTest(unittest.TestCase):
    def test_emits_structured_progress_bar_heartbeat_checkpoint_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checkpoint = root / "build.checkpoint.json"
            events: list[dict[str, object]] = []
            lifecycle = JobLifecycle(
                "canonical-build",
                checkpoint,
                input_fingerprint="sha256:input",
                progress=events.append,
                total_steps=4,
                heartbeat_interval_seconds=0,
            )

            lifecycle.started()
            lifecycle.step_started("source_import", 1)
            lifecycle.heartbeat("source_import")
            lifecycle.checkpoint("source_import", {"staged_db": "canonical.tmp"})
            lifecycle.step_completed("source_import", 1)

            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            self.assertEqual(payload["job"], "canonical-build")
            self.assertEqual(payload["input_fingerprint"], "sha256:input")
            self.assertEqual(payload["completed_phase"], "source_import")
            self.assertEqual(payload["artifacts"], {"staged_db": "canonical.tmp"})
            self.assertTrue(any(event["status"] == "heartbeat" for event in events))
            progress = next(event for event in events if event["status"] == "progress")
            self.assertEqual(progress["current"], 1)
            self.assertEqual(progress["total"], 4)
            self.assertRegex(str(progress["bar"]), r"^\[[#-]+\]$")

            lifecycle.completed()
            self.assertFalse(checkpoint.exists())
            self.assertEqual(events[-1]["status"], "completed")

    def test_matching_checkpoint_resumes_but_changed_input_discards_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "build.checkpoint.json"
            first = JobLifecycle(
                "integrated-build",
                checkpoint,
                input_fingerprint="sha256:first",
                progress=None,
            )
            first.checkpoint("substance", {"canonical": "stage.sqlite"})

            resumed = JobLifecycle(
                "integrated-build",
                checkpoint,
                input_fingerprint="sha256:first",
                progress=None,
            )
            self.assertEqual(resumed.completed_phase, "substance")
            self.assertEqual(resumed.artifacts, {"canonical": "stage.sqlite"})

            with self.assertRaisesRegex(RuntimeError, "checkpoint discarded.*input fingerprint changed"):
                JobLifecycle(
                    "integrated-build",
                    checkpoint,
                    input_fingerprint="sha256:second",
                    progress=None,
                )
            self.assertFalse(checkpoint.exists())

    def test_input_fingerprint_is_order_independent_and_binds_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("alpha", encoding="utf-8")
            second.write_text("beta", encoding="utf-8")

            before = fingerprint_inputs({"b": second, "a": first}, context={"schema": "11"})
            reordered = fingerprint_inputs({"a": first, "b": second}, context={"schema": "11"})
            self.assertEqual(before, reordered)

            second.write_text("changed", encoding="utf-8")
            after = fingerprint_inputs({"a": first, "b": second}, context={"schema": "11"})
            self.assertNotEqual(before, after)

    def test_sqlite_progress_handler_emits_rate_limited_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            events: list[dict[str, object]] = []
            lifecycle = JobLifecycle(
                "sqlite-job",
                Path(temp_dir) / "checkpoint.json",
                input_fingerprint="sha256:test",
                progress=events.append,
                heartbeat_interval_seconds=0,
            )
            database = sqlite3.connect(":memory:")
            try:
                with sqlite_heartbeat(database, lifecycle, "scan", virtual_machine_steps=1):
                    database.execute(
                        "WITH RECURSIVE x(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM x WHERE n<50) "
                        "SELECT sum(n) FROM x"
                    ).fetchone()
            finally:
                database.close()

            self.assertTrue(
                any(event["status"] == "heartbeat" and event["phase"] == "scan" for event in events)
            )


if __name__ == "__main__":
    unittest.main()