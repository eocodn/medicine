from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.release import (
    PATCH_FORMAT,
    apply_chunk_patch,
    compress_snapshot,
    create_chunk_patch,
    sha256_file,
)


class ReleaseArtifactPrimitiveTest(unittest.TestCase):
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
        source = self.write("mobile.sqlite", b"medicine-reference\n" * 10_000)
        first = self.root / "first.sqlite.gz"
        second = self.root / "second.sqlite.gz"

        one = compress_snapshot(source, first)
        two = compress_snapshot(source, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(one["sha256"], two["sha256"])
        with gzip.open(first, "rb") as handle:
            self.assertEqual(handle.read(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()