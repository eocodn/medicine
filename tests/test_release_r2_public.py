from __future__ import annotations

import unittest

from medicine_canonical.release_r2_public import audit_public_bucket


class FakeS3:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", ContinuationToken=None) -> dict:
        return {
            "Contents": [{"Key": key} for key in self.keys if key.startswith(Prefix)],
            "IsTruncated": False,
        }


class R2PublicAuditTest(unittest.TestCase):
    def test_reference_only_bucket_is_safe_to_expose(self) -> None:
        result = audit_public_bucket(
            FakeS3(
                [
                    "reference/v1/latest.json",
                    "reference/v1/full/abc.sqlite.gz",
                    "reference/v1/patch/abc-def.mpatch",
                ]
            ),
            "medicine-reference",
        )

        self.assertEqual(result["status"], "safe_to_expose")
        self.assertEqual(result["object_count"], 3)
        self.assertEqual(result["unexpected_keys"], [])

    def test_any_non_reference_object_blocks_public_exposure(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outside reference/v1/"):
            audit_public_bucket(
                FakeS3(["reference/v1/latest.json", "private/source.zip"]),
                "medicine-reference",
            )


if __name__ == "__main__":
    unittest.main()