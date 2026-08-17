from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.release_r2 import release_sequence_from_env, release_signer_from_env
from medicine_canonical.release_signing import (
    RELEASE_SIGNATURE_ALGORITHM,
    RELEASE_SIGNATURE_ENVELOPE_VERSION,
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


class ReleaseSigningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = ReleaseSigner.from_private_pem("test-2026", TEST_PRIVATE_KEY_PEM)
        self.manifest = {
            "schema_version": 1,
            "created_at": "2026-08-17T12:00:00Z",
            "dataset_id": "sha256:test-dataset",
            "target": {
                "schema_version": "8",
                "sha256": "a" * 64,
                "size_bytes": 1234,
            },
            "full": {
                "key": "reference/v1/full/example.sqlite.gz",
                "compression": "gzip",
                "sha256": "b" * 64,
                "size_bytes": 321,
            },
            "patches": [],
            "history": [],
        }
        self.payload = (
            json.dumps(
                self.manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def verify(self, raw: bytes, *, minimum_release_sequence: int | None = None) -> dict:
        return verify_signed_envelope(
            raw,
            {"test-2026": TEST_PUBLIC_KEY_PEM},
            minimum_release_sequence=minimum_release_sequence,
        )

    def test_signed_envelope_round_trips_exact_payload(self) -> None:
        envelope = self.signer.sign_payload(self.payload, release_sequence=7)
        raw = encode_signed_envelope(envelope)

        verified = self.verify(raw, minimum_release_sequence=6)

        self.assertEqual(envelope["envelope_version"], RELEASE_SIGNATURE_ENVELOPE_VERSION)
        self.assertEqual(envelope["algorithm"], RELEASE_SIGNATURE_ALGORITHM)
        self.assertEqual(verified["release_sequence"], 7)
        self.assertEqual(verified["key_id"], "test-2026")
        self.assertEqual(verified["payload_bytes"], self.payload)
        self.assertEqual(verified["manifest"], self.manifest)

    def test_signing_is_deterministic_for_the_same_release_identity(self) -> None:
        first = encode_signed_envelope(self.signer.sign_payload(self.payload, release_sequence=7))
        second = encode_signed_envelope(self.signer.sign_payload(self.payload, release_sequence=7))

        self.assertEqual(first, second)

    def test_payload_or_sequence_tampering_is_rejected(self) -> None:
        envelope = self.signer.sign_payload(self.payload, release_sequence=7)

        tampered_payload = dict(envelope)
        payload = bytearray(base64.b64decode(tampered_payload["payload_base64"], validate=True))
        payload[-2] ^= 1
        tampered_payload["payload_base64"] = base64.b64encode(payload).decode("ascii")
        with self.assertRaisesRegex(ValueError, "signature"):
            self.verify(encode_signed_envelope(tampered_payload))

        tampered_sequence = dict(envelope)
        tampered_sequence["release_sequence"] = 8
        with self.assertRaisesRegex(ValueError, "signature"):
            self.verify(encode_signed_envelope(tampered_sequence))

    def test_unknown_key_and_replay_are_rejected(self) -> None:
        envelope = self.signer.sign_payload(self.payload, release_sequence=7)
        raw = encode_signed_envelope(envelope)

        with self.assertRaisesRegex(ValueError, "untrusted release signing key"):
            verify_signed_envelope(raw, {"other": TEST_PUBLIC_KEY_PEM})
        with self.assertRaisesRegex(ValueError, "release sequence"):
            self.verify(raw, minimum_release_sequence=7)
        with self.assertRaisesRegex(ValueError, "release sequence"):
            self.verify(raw, minimum_release_sequence=8)

    def test_signer_rejects_invalid_sequence_and_key_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "release_sequence"):
            self.signer.sign_payload(self.payload, release_sequence=0)
        with self.assertRaisesRegex(ValueError, "key_id"):
            ReleaseSigner.from_private_pem("", TEST_PRIVATE_KEY_PEM)

    def test_publication_signing_environment_is_required_and_strict(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "REFERENCE_SIGNING_KEY_ID"):
                release_signer_from_env()
            with self.assertRaisesRegex(RuntimeError, "REFERENCE_RELEASE_SEQUENCE"):
                release_sequence_from_env()

        with patch.dict(
            os.environ,
            {
                "REFERENCE_SIGNING_KEY_ID": "test-2026",
                "REFERENCE_SIGNING_PRIVATE_KEY_PEM": TEST_PRIVATE_KEY_PEM.decode("ascii"),
                "REFERENCE_RELEASE_SEQUENCE": "123",
            },
            clear=True,
        ):
            self.assertEqual(release_signer_from_env().key_id, "test-2026")
            self.assertEqual(release_sequence_from_env(), 123)

        with patch.dict(os.environ, {"REFERENCE_RELEASE_SEQUENCE": "0"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "positive signed 64-bit"):
                release_sequence_from_env()


    def test_release_verify_envelope_cli_reports_verified_manifest_as_json(self) -> None:
        envelope = encode_signed_envelope(self.signer.sign_payload(self.payload, release_sequence=7))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            envelope_path = root / "latest.json"
            public_key_path = root / "public.pem"
            envelope_path.write_bytes(envelope)
            public_key_path.write_bytes(TEST_PUBLIC_KEY_PEM)
            output = io.StringIO()
            with redirect_stdout(output):
                status = canonical_main(
                    [
                        "release-verify-envelope",
                        "--envelope",
                        str(envelope_path),
                        "--public-key",
                        str(public_key_path),
                        "--key-id",
                        "test-2026",
                        "--minimum-sequence",
                        "6",
                        "--json",
                    ]
                )

        self.assertEqual(status, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "verified")
        self.assertEqual(payload["key_id"], "test-2026")
        self.assertEqual(payload["release_sequence"], 7)
        self.assertEqual(payload["manifest"], self.manifest)


if __name__ == "__main__":
    unittest.main()