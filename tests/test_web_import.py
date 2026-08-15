from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WebModuleImportTest(unittest.TestCase):
    def test_import_does_not_require_default_canonical_database(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as empty_cwd:
            env = os.environ.copy()
            env.pop("MEDICINE_CANONICAL_DB", None)
            env.pop("MEDICINE_PERSONAL_DB", None)
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, "-c", "import medicine_app.web"],
                cwd=empty_cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
