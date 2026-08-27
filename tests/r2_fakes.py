from __future__ import annotations

import hashlib
import io


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
            exc.response = {
                "ResponseMetadata": {"HTTPStatusCode": 412},
                "Error": {"Code": "PreconditionFailed"},
            }
            raise exc
        if "If-Match" in conditions:
            if existing is None or self.etag(existing["Body"]) != conditions["If-Match"]:
                exc = FakeNotFound()
                exc.response = {
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                    "Error": {"Code": "PreconditionFailed"},
                }
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

    def list_objects_v2(self, *, Bucket: str, Prefix: str = "", ContinuationToken=None) -> dict:
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