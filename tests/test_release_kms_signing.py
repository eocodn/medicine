from __future__ import annotations

import base64
import json
import os
import types
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from medicine_canonical.release_r2 import release_signer_from_env
from medicine_canonical.release_signing import (
    KmsReleaseSigner,
    encode_signed_envelope,
    verify_signed_envelope,
)

from tests.test_release_signing import TEST_PRIVATE_KEY_PEM, TEST_PUBLIC_KEY_PEM


class FakeKmsClient:
    def __init__(self) -> None:
        self.private_key = serialization.load_pem_private_key(TEST_PRIVATE_KEY_PEM, password=None)
        self.public_key_requests: list[dict] = []
        self.sign_requests: list[dict] = []

    def get_public_key(self, *, request: dict):
        self.public_key_requests.append(request)
        return types.SimpleNamespace(pem=TEST_PUBLIC_KEY_PEM.decode("ascii"))

    def asymmetric_sign(self, *, request: dict):
        self.sign_requests.append(request)
        digest = request["digest"]["sha256"]
        signature = self.private_key.sign(
            digest,
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
        return types.SimpleNamespace(signature=signature)


class KmsReleaseSigningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key_version = (
            "projects/test-project/locations/global/keyRings/medicine-release/"
            "cryptoKeys/reference-release-signing/cryptoKeyVersions/1"
        )
        self.payload = (
            json.dumps(
                {"schema_version": 1, "dataset_id": "sha256:test"},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def test_kms_signer_fetches_public_key_and_signs_sha256_digest(self) -> None:
        client = FakeKmsClient()
        signer = KmsReleaseSigner.from_client(
            key_id="test-2026",
            key_version=self.key_version,
            client=client,
        )

        envelope = signer.sign_payload(self.payload, release_sequence=7)
        raw = encode_signed_envelope(envelope)
        verified = verify_signed_envelope(raw, {"test-2026": TEST_PUBLIC_KEY_PEM})

        self.assertEqual(signer.public_key_pem(), TEST_PUBLIC_KEY_PEM)
        self.assertEqual(verified["payload_bytes"], self.payload)
        self.assertEqual(client.public_key_requests, [{"name": self.key_version}])
        self.assertEqual(len(client.sign_requests), 1)
        request = client.sign_requests[0]
        self.assertEqual(request["name"], self.key_version)
        self.assertEqual(len(request["digest"]["sha256"]), 32)
        self.assertNotIn("data", request)

    def test_kms_signer_rejects_malformed_key_version_and_bad_public_key(self) -> None:
        client = FakeKmsClient()
        with self.assertRaisesRegex(ValueError, "KMS key version"):
            KmsReleaseSigner.from_client(
                key_id="test-2026",
                key_version="projects/test/cryptoKeys/key",
                client=client,
            )

        client.get_public_key = lambda **_: types.SimpleNamespace(pem="not a public key")
        with self.assertRaisesRegex(ValueError, "public key"):
            KmsReleaseSigner.from_client(
                key_id="test-2026",
                key_version=self.key_version,
                client=client,
            )

    def test_publication_environment_requires_kms_key_version_not_private_pem(self) -> None:
        client = FakeKmsClient()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "REFERENCE_SIGNING_KMS_KEY_VERSION"):
                release_signer_from_env(kms_client=client)

        with patch.dict(
            os.environ,
            {
                "REFERENCE_SIGNING_KEY_ID": "test-2026",
                "REFERENCE_SIGNING_KMS_KEY_VERSION": self.key_version,
                "REFERENCE_SIGNING_PRIVATE_KEY_PEM": TEST_PRIVATE_KEY_PEM.decode("ascii"),
            },
            clear=True,
        ):
            signer = release_signer_from_env(kms_client=client)
            self.assertIsInstance(signer, KmsReleaseSigner)
            self.assertEqual(signer.key_version, self.key_version)

        envelope = signer.sign_payload(self.payload, release_sequence=9)
        self.assertTrue(base64.b64decode(envelope["signature_base64"], validate=True))


if __name__ == "__main__":
    unittest.main()