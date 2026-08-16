from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.release import sha256_file
from medicine_canonical.release_r2 import publish_release


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

    def put_object(self, *, Bucket: str, Key: str, Body, ContentType=None, Metadata=None, custom_headers=None) -> dict:
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
        }
        self.put_order.append(Key)
        return {}


class R2ReleasePublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakeS3()
        self.bucket = "medicine-reference"

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

    def test_first_publish_uploads_full_before_latest_and_round_trips_remote_state(self) -> None:
        db, manifest = self.mobile("one", b"A" * 500_000, "sha256:one")
        result = publish_release(
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
        latest = json.loads(self.client.objects[(self.bucket, latest_key)]["Body"])
        self.assertEqual(latest["dataset_id"], "sha256:one")
        self.assertEqual(latest["patches"], [])
        full = self.client.objects[(self.bucket, latest["full"]["key"])]
        self.assertEqual(full["Metadata"]["sha256"], latest["full"]["sha256"])

    def test_second_publish_downloads_verified_base_and_publishes_direct_patch(self) -> None:
        first_db, first_manifest = self.mobile("one", b"A" * 2_000_000, "sha256:one")
        publish_release(
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
        result = publish_release(
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

    def test_new_release_builds_direct_patches_from_recent_history_bases(self) -> None:
        base = bytearray(os.urandom(1_000_000))
        one_db, one_manifest = self.mobile("one-history", bytes(base), "sha256:one-history")
        publish_release(
            self.client, self.bucket, one_db, one_manifest, self.root / "dist-history-one",
            created_at="2026-08-16T10:00:00Z",
        )

        base[65_536:69_632] = b"2" * 4_096
        two_db, two_manifest = self.mobile("two-history", bytes(base), "sha256:two-history")
        publish_release(
            self.client, self.bucket, two_db, two_manifest, self.root / "dist-history-two",
            created_at="2026-08-16T11:00:00Z",
        )

        base[400_000:404_096] = b"3" * 4_096
        three_db, three_manifest = self.mobile("three-history", bytes(base), "sha256:three-history")
        result = publish_release(
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

    def test_same_dataset_id_is_idempotent_and_uploads_nothing(self) -> None:
        db, manifest = self.mobile("one", b"A" * 100_000, "sha256:one")
        publish_release(
            self.client,
            self.bucket,
            db,
            manifest,
            self.root / "dist-one",
            created_at="2026-08-16T11:00:00Z",
        )
        self.client.put_order.clear()
        result = publish_release(
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
        publish_release(
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
        with self.assertRaisesRegex(RuntimeError, "latest manifest changed"):
            publish_release(
                self.client,
                self.bucket,
                second_db,
                second_manifest,
                self.root / "dist-two",
                created_at="2026-08-16T12:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
