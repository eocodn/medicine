from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.release import prepare_release, sha256_file
from medicine_canonical.release_r2 import _read_latest, publish_release as publish_release_to_r2
from medicine_canonical.release_signing import (
    ReleaseSigner,
    encode_signed_envelope,
    verify_signed_envelope,
)


TEST_PRIVATE_KEY_PEM = b"""-----BEGIN EC PRIVATE KEY-----
MHcCAQEEINYQXOCBt5NbSlID2k5wrhlJSG5+jCgG9PpIwcftmU9boAoGCCqGSM49
AwEHoUQDQgAEPI67A47esbrnylrrO7WqAaSUwlSj9REIzwEkQlWQb4L3vx8tR5DS
Dl80GkuBe8cFmWJ4YtbS0n2nt4uKKPyxAA==
-----END EC PRIVATE KEY-----
"""

TEST_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEPI67A47esbrnylrrO7WqAaSUwlSj
9REIzwEkQlWQb4L3vx8tR5DSDl80GkuBe8cFmWJ4YtbS0n2nt4uKKPyxAA==
-----END PUBLIC KEY-----
"""


class FakeNotFound(Exception):
    def __init__(self) -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": 404},
            "Error": {"Code": "NoSuchKey"},
        }


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.put_order: list[str] = []
        self.delete_order: list[str] = []
        self.fail_delete_once: str | None = None
        self.before_latest_put = None

    @staticmethod
    def etag(body: bytes) -> str:
        return f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        record = self.objects.get((Bucket, Key))
        if record is None:
            raise FakeNotFound()
        return {
            "Body": io.BytesIO(record["Body"]),
            "ContentLength": len(record["Body"]),
            "Metadata": dict(record["Metadata"]),
            "ETag": self.etag(record["Body"]),
        }

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        record = self.objects.get((Bucket, Key))
        if record is None:
            raise FakeNotFound()
        return {
            "ContentLength": len(record["Body"]),
            "Metadata": dict(record["Metadata"]),
        }

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body,
        ContentType=None,
        CacheControl=None,
        Metadata=None,
        custom_headers=None,
    ) -> dict:
        if Key.endswith("latest.json") and self.before_latest_put is not None:
            callback, self.before_latest_put = self.before_latest_put, None
            callback()
        existing = self.objects.get((Bucket, Key))
        conditions = custom_headers or {}
        if conditions.get("If-None-Match") == "*" and existing is not None:
            exc = FakeNotFound()
            exc.response = {"ResponseMetadata": {"HTTPStatusCode": 412}, "Error": {"Code": "PreconditionFailed"}}
            raise exc
        if "If-Match" in conditions:
            if existing is None or self.etag(existing["Body"]) != conditions["If-Match"]:
                exc = FakeNotFound()
                exc.response = {"ResponseMetadata": {"HTTPStatusCode": 412}, "Error": {"Code": "PreconditionFailed"}}
                raise exc
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = {
            "Body": body,
            "Metadata": dict(Metadata or {}),
            "ContentType": ContentType,
            "CacheControl": CacheControl,
        }
        self.put_order.append(Key)
        return {}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken=None) -> dict:
        keys = sorted(
            key
            for bucket, key in self.objects
            if bucket == Bucket and key.startswith(Prefix)
        )
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        if self.fail_delete_once == Key:
            self.fail_delete_once = None
            raise RuntimeError("simulated delete failure")
        self.objects.pop((Bucket, Key), None)
        self.delete_order.append(Key)
        return {}


class R2ReleasePublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakeS3()
        self.bucket = "medicine-reference"
        self.signer = ReleaseSigner.from_private_pem("test-2026", TEST_PRIVATE_KEY_PEM)
        self.next_release_sequence = 1000

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def mobile(self, name: str, data: bytes, dataset_id: str) -> tuple[Path, Path]:
        db = self.root / f"{name}.sqlite"
        manifest = self.root / f"{name}.manifest.json"
        db.write_bytes(data)
        manifest.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "schema_version": "8",
                    "sha256": sha256_file(db),
                    "size_bytes": db.stat().st_size,
                }
            ),
            encoding="utf-8",
        )
        return db, manifest

    def seed_legacy_unsigned_release(
        self,
        db: Path,
        mobile_manifest: Path,
        *,
        created_at: str,
    ) -> dict:
        prepared = prepare_release(
            db,
            mobile_manifest,
            self.root / "legacy-dist",
            created_at=created_at,
        )
        manifest = prepared["manifest"]
        full_path = Path(prepared["full_path"])
        full = manifest["full"]
        self.client.objects[(self.bucket, full["key"])] = {
            "Body": full_path.read_bytes(),
            "Metadata": {"sha256": full["sha256"]},
            "ContentType": "application/gzip",
        }
        latest_body = Path(prepared["manifest_path"]).read_bytes()
        self.client.objects[(self.bucket, "reference/v1/latest.json")] = {
            "Body": latest_body,
            "Metadata": {"sha256": hashlib.sha256(latest_body).hexdigest()},
            "ContentType": "application/json",
        }
        return manifest

    def verify_latest(self) -> dict:
        raw = self.client.objects[(self.bucket, "reference/v1/latest.json")]["Body"]
        return verify_signed_envelope(raw, {"test-2026": TEST_PUBLIC_KEY_PEM})

    def publish_release(self, *args, release_sequence: int | None = None, **kwargs) -> dict:
        if release_sequence is None:
            release_sequence = self.next_release_sequence
            self.next_release_sequence += 1
        kwargs.setdefault("trusted_public_keys", {"test-2026": TEST_PUBLIC_KEY_PEM})
        return publish_release_to_r2(
            *args,
            signer=self.signer,
            release_sequence=release_sequence,
            **kwargs,
        )

    def test_unchanged_legacy_unsigned_release_is_migrated_to_signed_envelope(self) -> None:
        db, manifest = self.mobile("legacy", b"A" * 500_000, "sha256:legacy")
        legacy = self.seed_legacy_unsigned_release(
            db,
            manifest,
            created_at="2026-08-17T10:00:00Z",
        )

        result = self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-legacy-migration",
            created_at="2026-08-17T11:00:00Z",
            release_sequence=41,
        )

        verified = self.verify_latest()
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(verified["release_sequence"], 41)
        self.assertEqual(verified["manifest"], legacy)
        self.assertEqual(self.client.put_order, ["reference/v1/latest.json"])

    def test_signed_legacy_latest_rejects_unsupported_schema(self) -> None:
        payload = json.dumps(
            {"schema_version": 2, "dataset_id": "sha256:future-legacy-format"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        body = encode_signed_envelope(self.signer.sign_payload(payload, release_sequence=42))
        self.client.objects[(self.bucket, "reference/v1/latest.json")] = {
            "Body": body,
            "Metadata": {"sha256": hashlib.sha256(body).hexdigest()},
            "ContentType": "application/json",
        }

        with self.assertRaisesRegex(ValueError, "schema"):
            _read_latest(
                self.client,
                self.bucket,
                "reference/v1/latest.json",
                trusted_public_keys={"test-2026": TEST_PUBLIC_KEY_PEM},
            )

    def test_signed_release_requires_increasing_sequence_for_new_dataset(self) -> None:
        first_db, first_manifest = self.mobile("signed-one", b"A" * 500_000, "sha256:signed-one")
        self.publish_release(
            self.client,
            self.bucket,
            first_db,
            first_manifest,
            self.root / "dist-signed-one",
            created_at="2026-08-17T10:00:00Z",
            release_sequence=50,
        )
        second_db, second_manifest = self.mobile(
            "signed-two", b"A" * 490_000 + b"B" * 10_000, "sha256:signed-two"
        )

        with self.assertRaisesRegex(ValueError, "release_sequence"):
            self.publish_release(
                self.client,
                self.bucket,
                second_db,
                second_manifest,
                self.root / "dist-signed-two",
                created_at="2026-08-17T11:00:00Z",
                    release_sequence=50,
            )

    def test_signed_same_dataset_is_idempotent_without_resigning(self) -> None:
        db, manifest = self.mobile("signed-same", b"A" * 500_000, "sha256:signed-same")
        self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-signed-same",
            created_at="2026-08-17T10:00:00Z",
            release_sequence=60,
        )
        self.client.put_order.clear()

        result = self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-signed-same-retry",
            created_at="2026-08-17T11:00:00Z",
            release_sequence=61,
        )

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.verify_latest()["release_sequence"], 60)
        self.assertEqual(self.client.put_order, [])

    def test_first_publish_uploads_full_before_latest_and_round_trips_remote_state(self) -> None:
        db, manifest = self.mobile("one", b"A" * 500_000, "sha256:one")
        result = self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-one",
            created_at="2026-08-16T11:00:00Z",
        )

        self.assertEqual(result["status"], "published")
        latest_key = "reference/v1/latest.json"
        self.assertEqual(self.client.put_order[-1], latest_key)
        latest = self.verify_latest()["manifest"]
        self.assertEqual(latest["dataset_id"], "sha256:one")
        self.assertEqual(latest["patches"], [])
        full = self.client.objects[(self.bucket, latest["full"]["key"])]
        self.assertEqual(full["Metadata"]["sha256"], latest["full"]["sha256"])

    def test_second_publish_downloads_verified_base_and_publishes_direct_patch(self) -> None:
        first_db, first_manifest = self.mobile("one", b"A" * 2_000_000, "sha256:one")
        self.publish_release(
            self.client,
            self.bucket,
            first_db,
            first_manifest,
            self.root / "dist-one",
            created_at="2026-08-16T11:00:00Z",
        )
        self.client.put_order.clear()

        second_db, second_manifest = self.mobile(
            "two", b"A" * 1_900_000 + b"B" * 100_000, "sha256:two"
        )
        result = self.publish_release(
            self.client,
            self.bucket,
            second_db,
            second_manifest,
            self.root / "dist-two",
            created_at="2026-08-16T12:00:00Z",
        )
        latest = result["manifest"]

        self.assertEqual(result["status"], "published")
        self.assertEqual(len(latest["patches"]), 1)
        self.assertEqual(latest["patches"][0]["from_dataset_id"], "sha256:one")
        self.assertEqual(latest["patches"][0]["from_sha256"], sha256_file(first_db))
        self.assertEqual(self.client.put_order[-1], "reference/v1/latest.json")

    def test_public_release_objects_have_explicit_cache_semantics(self) -> None:
        db, manifest = self.mobile("cache-policy", b"A" * 500_000, "sha256:cache-policy")
        result = self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-cache-policy",
            created_at="2026-08-19T01:00:00Z",
            release_sequence=70,
        )

        full_key = result["manifest"]["full"]["key"]
        self.assertEqual(
            self.client.objects[(self.bucket, full_key)]["CacheControl"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            self.client.objects[(self.bucket, "reference/v1/latest.json")]["CacheControl"],
            "no-store",
        )

    def test_new_release_builds_direct_patches_from_recent_history_bases(self) -> None:
        base = bytearray(os.urandom(1_000_000))
        one_db, one_manifest = self.mobile("one-history", bytes(base), "sha256:one-history")
        self.publish_release(
            self.client, self.bucket, one_db, one_manifest, self.root / "dist-history-one",
            created_at="2026-08-16T10:00:00Z",
        )

        base[65_536:69_632] = b"2" * 4_096
        two_db, two_manifest = self.mobile("two-history", bytes(base), "sha256:two-history")
        self.publish_release(
            self.client, self.bucket, two_db, two_manifest, self.root / "dist-history-two",
            created_at="2026-08-16T11:00:00Z",
        )

        base[400_000:404_096] = b"3" * 4_096
        three_db, three_manifest = self.mobile("three-history", bytes(base), "sha256:three-history")
        result = self.publish_release(
            self.client, self.bucket, three_db, three_manifest, self.root / "dist-history-three",
            created_at="2026-08-16T12:00:00Z",
        )

        patches = result["manifest"]["patches"]
        self.assertEqual(
            {patch["from_dataset_id"] for patch in patches},
            {"sha256:one-history", "sha256:two-history"},
        )
        self.assertEqual(
            [entry["dataset_id"] for entry in result["manifest"]["history"]],
            ["sha256:two-history", "sha256:one-history"],
        )

    def test_fourth_publish_retains_three_full_snapshots_and_only_current_patches(self) -> None:
        base = bytearray(os.urandom(1_000_000))
        manifests = []
        for index, label in enumerate(("one", "two", "three", "four"), start=1):
            if index > 1:
                offset = index * 100_000
                base[offset:offset + 4_096] = bytes([64 + index]) * 4_096
            db, mobile_manifest = self.mobile(
                f"retention-{label}",
                bytes(base),
                f"sha256:retention-{label}",
            )
            result = self.publish_release(
                self.client,
                self.bucket,
                db,
                mobile_manifest,
                self.root / f"dist-retention-{label}",
                created_at=f"2026-08-16T{9 + index:02d}:00:00Z",
            )
            manifests.append(result["manifest"])

        latest = manifests[-1]
        self.assertEqual(
            [entry["dataset_id"] for entry in latest["history"]],
            ["sha256:retention-three", "sha256:retention-two"],
        )

        full_prefix = "reference/v1/full/"
        full_keys = {
            key
            for bucket, key in self.client.objects
            if bucket == self.bucket and key.startswith(full_prefix)
        }
        expected_full_keys = {latest["full"]["key"]} | {
            entry["full"]["key"] for entry in latest["history"]
        }
        self.assertEqual(full_keys, expected_full_keys)
        self.assertEqual(len(full_keys), 3)

        patch_prefix = "reference/v1/patch/"
        patch_keys = {
            key
            for bucket, key in self.client.objects
            if bucket == self.bucket and key.startswith(patch_prefix)
        }
        self.assertEqual(patch_keys, {patch["key"] for patch in latest["patches"]})
        self.assertTrue(
            any(key.startswith(full_prefix) for key in self.client.delete_order),
            self.client.delete_order,
        )
        self.assertTrue(
            any(key.startswith(patch_prefix) for key in self.client.delete_order),
            self.client.delete_order,
        )

    def test_client_older_than_retained_full_window_has_no_direct_patch(self) -> None:
        base = bytearray(os.urandom(1_000_000))
        latest = None
        first_sha = None
        for index, label in enumerate(("one", "two", "three", "four", "five"), start=1):
            if index > 1:
                offset = index * 120_000
                base[offset:offset + 4_096] = bytes([70 + index]) * 4_096
            db, mobile_manifest = self.mobile(
                f"fallback-{label}",
                bytes(base),
                f"sha256:fallback-{label}",
            )
            if first_sha is None:
                first_sha = sha256_file(db)
            latest = self.publish_release(
                self.client,
                self.bucket,
                db,
                mobile_manifest,
                self.root / f"dist-fallback-{label}",
                created_at=f"2026-08-16T{8 + index:02d}:00:00Z",
            )["manifest"]

        self.assertIsNotNone(latest)
        self.assertNotIn(first_sha, {patch["from_sha256"] for patch in latest["patches"]})
        self.assertEqual(
            {patch["from_dataset_id"] for patch in latest["patches"]},
            {
                "sha256:fallback-two",
                "sha256:fallback-three",
                "sha256:fallback-four",
            },
        )

    def test_same_dataset_retry_repairs_retention_after_post_publish_cleanup_failure(self) -> None:
        base = bytearray(os.urandom(1_000_000))
        one_db, one_manifest = self.mobile("cleanup-one", bytes(base), "sha256:cleanup-one")
        self.publish_release(
            self.client, self.bucket, one_db, one_manifest, self.root / "dist-cleanup-one",
            created_at="2026-08-16T10:00:00Z",
        )

        base[100_000:104_096] = b"2" * 4_096
        two_db, two_manifest = self.mobile("cleanup-two", bytes(base), "sha256:cleanup-two")
        second = self.publish_release(
            self.client, self.bucket, two_db, two_manifest, self.root / "dist-cleanup-two",
            created_at="2026-08-16T11:00:00Z",
        )
        stale_patch = second["manifest"]["patches"][0]["key"]
        self.client.fail_delete_once = stale_patch

        base[200_000:204_096] = b"3" * 4_096
        three_db, three_manifest = self.mobile("cleanup-three", bytes(base), "sha256:cleanup-three")
        with self.assertRaisesRegex(RuntimeError, "retention cleanup failed"):
            self.publish_release(
                self.client, self.bucket, three_db, three_manifest, self.root / "dist-cleanup-three",
                created_at="2026-08-16T12:00:00Z",
            )

        latest = self.verify_latest()["manifest"]
        self.assertEqual(latest["dataset_id"], "sha256:cleanup-three")
        self.assertIn((self.bucket, stale_patch), self.client.objects)

        retried = self.publish_release(
            self.client, self.bucket, three_db, three_manifest, self.root / "dist-cleanup-three-retry",
            created_at="2026-08-16T12:30:00Z",
        )
        self.assertEqual(retried["status"], "unchanged")
        self.assertEqual(retried["cleanup"]["deleted"], [stale_patch])
        self.assertNotIn((self.bucket, stale_patch), self.client.objects)

    def test_same_dataset_id_is_idempotent_and_uploads_nothing(self) -> None:
        db, manifest = self.mobile("one", b"A" * 100_000, "sha256:one")
        self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-one",
            created_at="2026-08-16T11:00:00Z",
        )
        self.client.put_order.clear()
        result = self.publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-repeat",
            created_at="2026-08-16T12:00:00Z",
        )
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.client.put_order, [])

    def test_publisher_refuses_to_advance_latest_when_remote_state_changes(self) -> None:
        first_db, first_manifest = self.mobile("one", b"A" * 200_000, "sha256:one")
        self.publish_release(
            self.client,
            self.bucket,
            first_db,
            first_manifest,
            self.root / "dist-one",
            created_at="2026-08-16T11:00:00Z",
        )
        second_db, second_manifest = self.mobile("two", b"A" * 190_000 + b"B" * 10_000, "sha256:two")

        def mutate_latest() -> None:
            key = (self.bucket, "reference/v1/latest.json")
            current = dict(self.client.objects[key])
            current["Body"] = current["Body"] + b" "
            self.client.objects[key] = current

        self.client.before_latest_put = mutate_latest
        self.client.delete_order.clear()
        with self.assertRaisesRegex(RuntimeError, "latest manifest changed"):
            self.publish_release(
                self.client,
                self.bucket,
                second_db,
                second_manifest,
                self.root / "dist-two",
                created_at="2026-08-16T12:00:00Z",
            )
        self.assertEqual(self.client.delete_order, [])


if __name__ == "__main__":
    unittest.main()
