from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.release_r2_object_io import _download_to_file
from tests.r2_fakes import FakeS3


class R2ObjectIoTest(unittest.TestCase):
    def test_object_download_reports_bounded_streaming_progress(self) -> None:
        payload = b"A" * (17 * 1024 * 1024)
        key = "reference/v2/contracts/1/full/large.sqlite.gz"
        client = FakeS3()
        bucket = "medicine-reference"
        client.objects[(bucket, key)] = {
            "Body": payload,
            "Metadata": {"sha256": hashlib.sha256(payload).hexdigest()},
        }
        progress: list[tuple[int, int]] = []

        with tempfile.TemporaryDirectory() as tmp:
            result = _download_to_file(
                client,
                bucket,
                key,
                Path(tmp) / "large.sqlite.gz",
                progress=lambda current, total: progress.append((current, total)),
            )

        self.assertEqual(result["size_bytes"], len(payload))
        self.assertEqual(
            progress,
            [
                (8 * 1024 * 1024, len(payload)),
                (16 * 1024 * 1024, len(payload)),
                (17 * 1024 * 1024, len(payload)),
            ],
        )


if __name__ == "__main__":
    unittest.main()