from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from medicine_canonical.snapshot_io import (
    canonical_json,
    insert_source_snapshot,
    load_snapshot_metadata,
    sha256_file,
    snapshot_metadata_path,
)


class SnapshotIoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _snapshot(self) -> tuple[Path, dict]:
        path = self.root / "sample.jsonl"
        path.write_text('{"b":2,"a":1}\n', encoding="utf-8")
        metadata = {
            "dataset_key": "mfds_dur:test",
            "source_family": "mfds_dur_item_api",
            "source_locator": "https://example.test/test",
            "fetched_at": "2026-08-18T19:00:00+09:00",
            "row_count": 1,
            "reported_row_count": 1,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "page_size": 500,
        }
        snapshot_metadata_path(path).write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        return path, metadata

    def test_load_snapshot_metadata_validates_required_fields_and_hash(self) -> None:
        path, metadata = self._snapshot()

        self.assertEqual(load_snapshot_metadata(path, label="API snapshot"), metadata)
        self.assertEqual(sha256_file(path), metadata["sha256"])

        path.write_text('{"tampered":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sha256 mismatch for API snapshot"):
            load_snapshot_metadata(path, label="API snapshot")

    def test_load_snapshot_metadata_rejects_missing_required_field(self) -> None:
        path, metadata = self._snapshot()
        metadata.pop("source_locator")
        snapshot_metadata_path(path).write_text(json.dumps(metadata), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, r"missing \['source_locator'\]"):
            load_snapshot_metadata(path, label="API snapshot")

    def test_insert_source_snapshot_preserves_provenance_payload(self) -> None:
        path, metadata = self._snapshot()
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                """CREATE TABLE source_snapshots(
                    dataset_key TEXT, source_family TEXT, source_locator TEXT,
                    snapshot_path TEXT, fetched_at TEXT, row_count INTEGER,
                    reported_row_count INTEGER, sha256 TEXT, metadata_json TEXT
                )"""
            )
            insert_source_snapshot(con, metadata, path)
            row = con.execute("SELECT * FROM source_snapshots").fetchone()
        finally:
            con.close()

        self.assertEqual(
            row,
            (
                metadata["dataset_key"],
                metadata["source_family"],
                metadata["source_locator"],
                str(path),
                metadata["fetched_at"],
                1,
                1,
                metadata["sha256"],
                canonical_json(metadata),
            ),
        )

    def test_builders_do_not_reimplement_snapshot_mechanics(self) -> None:
        for path in (
            Path("medicine_canonical/build.py"),
            Path("medicine_canonical/mfds_ingredient.py"),
            Path("medicine_canonical/sources.py"),
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("def _sha256(", source)
            self.assertNotIn("def _insert_source_snapshot(", source)
        self.assertNotIn("def _load_meta(", Path("medicine_canonical/build.py").read_text())
        self.assertNotIn(
            "def _load_snapshot_meta(",
            Path("medicine_canonical/mfds_ingredient.py").read_text(),
        )
        self.assertNotIn("def _canonical_json(", Path("medicine_canonical/sources.py").read_text())


if __name__ == "__main__":
    unittest.main()