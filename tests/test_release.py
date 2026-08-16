from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.release import (
    PATCH_FORMAT,
    apply_chunk_patch,
    compress_snapshot,
    create_chunk_patch,
    prepare_release,
    sha256_file,
)


class ReleaseArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_chunk_patch_reconstructs_exact_target_bytes(self) -> None:
        source = self.write("old.sqlite", b"A" * 200_000 + b"B" * 90_000)
        target = self.write("new.sqlite", b"A" * 200_000 + b"C" * 90_000 + b"tail")
        patch = self.root / "update.mpatch"
        rebuilt = self.root / "rebuilt.sqlite"

        created = create_chunk_patch(source, target, patch, chunk_size=64 * 1024)
        applied = apply_chunk_patch(source, patch, rebuilt)

        self.assertEqual(created["format"], PATCH_FORMAT)
        self.assertGreater(created["changed_chunks"], 0)
        self.assertEqual(rebuilt.read_bytes(), target.read_bytes())
        self.assertEqual(applied["sha256"], sha256_file(target))
        self.assertEqual(applied["size_bytes"], target.stat().st_size)

    def test_patch_rejects_wrong_source_and_corruption(self) -> None:
        source = self.write("old.sqlite", b"A" * 100_000)
        target = self.write("new.sqlite", b"A" * 50_000 + b"B" * 50_000)
        patch = self.root / "update.mpatch"
        create_chunk_patch(source, target, patch, chunk_size=16 * 1024)

        wrong_source = self.write("wrong.sqlite", b"Z" * 100_000)
        with self.assertRaisesRegex(ValueError, "source SHA-256"):
            apply_chunk_patch(wrong_source, patch, self.root / "wrong-output.sqlite")

        raw = bytearray(patch.read_bytes())
        raw[-1] ^= 0xFF
        patch.write_bytes(raw)
        with self.assertRaises((ValueError, RuntimeError, EOFError, OSError)):
            apply_chunk_patch(source, patch, self.root / "corrupt-output.sqlite")

    def test_full_snapshot_compression_is_deterministic_and_round_trips(self) -> None:
        source = self.write("mobile.sqlite", (b"medicine-reference\n" * 10_000))
        first = self.root / "first.sqlite.gz"
        second = self.root / "second.sqlite.gz"
        one = compress_snapshot(source, first)
        two = compress_snapshot(source, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(one["sha256"], two["sha256"])
        with gzip.open(first, "rb") as handle:
            self.assertEqual(handle.read(), source.read_bytes())

    def test_prepare_release_emits_verified_patch_and_full_fallback(self) -> None:
        old = self.write("old.sqlite", b"A" * 2_000_000)
        new = self.write("new.sqlite", b"A" * 1_900_000 + b"B" * 100_000)
        mobile_manifest = self.root / "mobile.manifest.json"
        mobile_manifest.write_text(
            json.dumps(
                {
                    "dataset_id": "sha256:dataset-new",
                    "schema_version": "8",
                    "sha256": sha256_file(new),
                    "size_bytes": new.stat().st_size,
                }
            ),
            encoding="utf-8",
        )

        result = prepare_release(
            new,
            mobile_manifest,
            self.root / "dist",
            previous_db=old,
            previous_dataset_id="sha256:dataset-old",
            created_at="2026-08-16T11:00:00Z",
            chunk_size=64 * 1024,
        )

        latest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(latest["schema_version"], 1)
        self.assertEqual(latest["dataset_id"], "sha256:dataset-new")
        self.assertEqual(latest["target"]["sha256"], sha256_file(new))
        self.assertEqual(latest["full"]["compression"], "gzip")
        self.assertEqual(len(latest["patches"]), 1)
        self.assertEqual(latest["patches"][0]["from_dataset_id"], "sha256:dataset-old")
        self.assertEqual(latest["patches"][0]["format"], PATCH_FORMAT)

        patch_path = self.root / "dist" / latest["patches"][0]["key"]
        rebuilt = self.root / "verified.sqlite"
        apply_chunk_patch(old, patch_path, rebuilt)
        self.assertEqual(sha256_file(rebuilt), latest["target"]["sha256"])

    def test_release_cli_create_and_apply_round_trip(self) -> None:
        base = bytearray(os.urandom(300_000))
        old = self.write("cli-old.sqlite", bytes(base))
        base[131_072:135_168] = b"B" * 4_096
        new = self.write("cli-new.sqlite", bytes(base))
        mobile_manifest = self.root / "cli-mobile.manifest.json"
        mobile_manifest.write_text(
            json.dumps(
                {
                    "dataset_id": "sha256:cli-new",
                    "schema_version": "8",
                    "sha256": sha256_file(new),
                    "size_bytes": new.stat().st_size,
                }
            ),
            encoding="utf-8",
        )
        dist = self.root / "cli-dist"
        self.assertEqual(
            canonical_main([
                "release-create", "--db", str(new), "--mobile-manifest", str(mobile_manifest),
                "--output-dir", str(dist), "--previous-db", str(old),
                "--previous-dataset-id", "sha256:cli-old", "--created-at", "2026-08-16T11:00:00Z", "--json",
            ]),
            0,
        )
        latest = json.loads((dist / "reference/v1/latest.json").read_text())
        patch = dist / latest["patches"][0]["key"]
        rebuilt = self.root / "cli-rebuilt.sqlite"
        self.assertEqual(
            canonical_main(["release-apply", "--source", str(old), "--patch", str(patch), "--output", str(rebuilt), "--json"]),
            0,
        )
        self.assertEqual(rebuilt.read_bytes(), new.read_bytes())

    def test_prepare_release_omits_nonbeneficial_patch(self) -> None:
        old = self.write("old.sqlite", os.urandom(350_000))
        new = self.write("new.sqlite", os.urandom(350_000))
        mobile_manifest = self.root / "mobile.manifest.json"
        mobile_manifest.write_text(
            json.dumps(
                {
                    "dataset_id": "sha256:new-random",
                    "schema_version": "8",
                    "sha256": sha256_file(new),
                    "size_bytes": new.stat().st_size,
                }
            ),
            encoding="utf-8",
        )

        result = prepare_release(
            new,
            mobile_manifest,
            self.root / "dist-random",
            previous_db=old,
            previous_dataset_id="sha256:old-random",
            created_at="2026-08-16T11:00:00Z",
            chunk_size=16 * 1024,
        )
        latest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual(latest["patches"], [])


if __name__ == "__main__":
    unittest.main()
