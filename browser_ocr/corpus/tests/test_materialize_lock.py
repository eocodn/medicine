from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class MaterializeLockTest(unittest.TestCase):
    def test_advisory_lock_blocks_only_while_owner_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lock = Path(raw) / ".materialize.lock"
            command = [
                sys.executable,
                "-m",
                "browser_ocr.corpus.materialize_lock",
                "--path",
                str(lock),
            ]
            first = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert first.stdout is not None
                self.assertEqual(json.loads(first.stdout.readline())["status"], "locked")
                second = subprocess.run(command, input="", capture_output=True, text=True, check=False)
                self.assertEqual(second.returncode, 2)
                self.assertEqual(json.loads(second.stdout)["status"], "busy")
            finally:
                if first.stdin is not None:
                    first.stdin.close()
                self.assertEqual(first.wait(timeout=5), 0)
                if first.stdout is not None:
                    first.stdout.close()
                if first.stderr is not None:
                    first.stderr.close()

            third = subprocess.run(command, input="", capture_output=True, text=True, check=False)
            self.assertEqual(third.returncode, 0)
            self.assertEqual(json.loads(third.stdout)["status"], "locked")


if __name__ == "__main__":
    unittest.main()