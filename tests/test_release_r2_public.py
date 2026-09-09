from __future__ import annotations

import unittest

from medicine_canonical.release_r2_public import (
    audit_public_bucket,
    retire_reference_namespace,
)


class FakeS3:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", ContinuationToken=None) -> dict:
        return {
            "Contents": [{"Key": key} for key in self.keys if key.startswith(Prefix)],
            "IsTruncated": False,
        }

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.keys.remove(Key)


class R2PublicAuditTest(unittest.TestCase):
    def test_reference_only_bucket_is_safe_to_expose(self) -> None:
        result = audit_public_bucket(
            FakeS3(
                [
                    "reference/v2/latest.json",
                    "reference/v2/contracts/1/full/abc.sqlite.gz",
                    "reference/v2/contracts/1/patch/abc-def.mpatch",
                ]
            ),
            "medicine-reference",
        )

        self.assertEqual(result["status"], "safe_to_expose")
        self.assertEqual(result["object_count"], 3)
        self.assertEqual(result["unexpected_keys"], [])

    def test_any_non_reference_object_blocks_public_exposure(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "outside public reference namespaces"):
            audit_public_bucket(
                FakeS3(["reference/v2/latest.json", "private/source.zip"]),
                "medicine-reference",
            )

    def test_retirement_deletes_only_the_explicit_inactive_namespace(self) -> None:
        client = FakeS3(
            [
                "reference/v1/latest.json",
                "reference/v1/full/abc.sqlite.gz",
                "reference/v2/latest.json",
            ]
        )

        result = retire_reference_namespace(client, "medicine-reference", "reference/v1/")

        self.assertEqual(result["status"], "retired")
        self.assertEqual(result["namespace"], "reference/v1/")
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(client.keys, ["reference/v2/latest.json"])

    def test_retirement_rejects_active_or_partial_namespaces(self) -> None:
        client = FakeS3(["reference/v2/latest.json"])

        for namespace in ("reference/v2/", "reference/", "reference/v1", "private/v1/"):
            with self.subTest(namespace=namespace):
                with self.assertRaises(ValueError):
                    retire_reference_namespace(client, "medicine-reference", namespace)

    def test_retirement_fails_when_deleted_objects_remain_authoritatively_listed(self) -> None:
        class StickyDeleteS3(FakeS3):
            def delete_object(self, *, Bucket: str, Key: str) -> None:
                pass

        with self.assertRaisesRegex(RuntimeError, "still contains objects"):
            retire_reference_namespace(
                StickyDeleteS3(["reference/v1/latest.json"]),
                "medicine-reference",
                "reference/v1/",
            )


if __name__ == "__main__":
    unittest.main()
