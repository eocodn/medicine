from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from medicine_app.reference_update import MOBILE_DATA_POLICY_VERSION, verify_reference_database
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mobile import MOBILE_DATA_POLICY_VERSION as BUILDER_POLICY_VERSION, build_mobile_database
from tests.test_safety_coverage import make_canonical_db


class ReferenceUpdateRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical.sqlite"
        self.mobile = self.root / "mobile.sqlite"
        self.manifest = self.root / "mobile.manifest.json"
        make_canonical_db(self.canonical)
        self.release = build_mobile_database(self.canonical, self.mobile, manifest_path=self.manifest)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_runtime_verifier_accepts_exact_mobile_release_identity(self) -> None:
        result = verify_reference_database(
            self.mobile,
            expected_schema_version=self.release["schema_version"],
            expected_dataset_id=self.release["dataset_id"],
        )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["dataset_id"], self.release["dataset_id"])
        self.assertEqual(result["schema_version"], "9")

    def test_runtime_verifier_rejects_dataset_identity_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "dataset identity"):
            verify_reference_database(
                self.mobile,
                expected_schema_version="9",
                expected_dataset_id="sha256:" + "0" * 64,
            )

    def test_runtime_verifier_rejects_runtime_source_policy_tampering(self) -> None:
        with sqlite3.connect(self.mobile) as con:
            con.execute("DELETE FROM source_snapshots WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'")
            con.commit()
        with self.assertRaisesRegex(ValueError, "runtime policy"):
            verify_reference_database(
                self.mobile,
                expected_schema_version="9",
                expected_dataset_id=self.release["dataset_id"],
            )

    def test_runtime_policy_version_is_shared_with_mobile_builder(self) -> None:
        self.assertEqual(MOBILE_DATA_POLICY_VERSION, BUILDER_POLICY_VERSION)

    def test_headless_cli_verifies_mobile_runtime_database(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = canonical_main([
                "mobile-verify-runtime",
                "--db", str(self.mobile),
                "--schema-version", "9",
                "--dataset-id", self.release["dataset_id"],
                "--json",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["dataset_id"], self.release["dataset_id"])


if __name__ == "__main__":
    unittest.main()
