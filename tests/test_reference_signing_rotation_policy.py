from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from medicine_canonical.release_signing import ReleaseSigner, encode_signed_envelope
from medicine_canonical.release_window import publish_contract_window, publish_verified_contract_window
from tests.r2_fakes import TEST_PRIVATE_KEY_PEM


class ReferenceSigningRotationPolicyTest(unittest.TestCase):
    def test_publishers_require_an_authoritative_trust_set(self) -> None:
        for publisher in (publish_contract_window, publish_verified_contract_window):
            parameter = inspect.signature(publisher).parameters["trusted_public_keys"]
            self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_release_gate_loads_trust_from_manifest_not_kotlin_source(self) -> None:
        script = Path("scripts/verify-reference-contract-root.py")
        self.assertNotIn("ReferenceTrust.kt", script.read_text())
        spec = importlib.util.spec_from_file_location("verify_reference_contract_root", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        old_signer = ReleaseSigner.from_private_pem("old-key", TEST_PRIVATE_KEY_PEM)
        new_private = ec.generate_private_key(ec.SECP256R1())
        new_private_pem = new_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        new_signer = ReleaseSigner.from_private_pem("new-key", new_private_pem)

        def entry(signer: ReleaseSigner) -> dict:
            public_key = serialization.load_pem_public_key(signer.public_key_pem())
            der = public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            return {
                "key_id": signer.key_id,
                "public_key_pem": signer.public_key_pem().decode("ascii"),
                "spki_sha256": hashlib.sha256(der).hexdigest(),
            }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as manifest:
            json.dump(
                {
                    "active_key_id": "old-key",
                    "keys": [entry(old_signer), entry(new_signer)],
                },
                manifest,
            )
            manifest.flush()
            trusted = module._trusted_public_keys_from_manifest(Path(manifest.name))

        self.assertEqual(set(trusted), {"old-key", "new-key"})

    def test_release_gate_accepts_either_key_during_overlap_and_rejects_unknown_key(self) -> None:
        script = Path("scripts/verify-reference-contract-root.py")
        spec = importlib.util.spec_from_file_location("verify_reference_contract_root", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        old_signer = ReleaseSigner.from_private_pem("old-key", TEST_PRIVATE_KEY_PEM)
        new_private = ec.generate_private_key(ec.SECP256R1())
        new_private_pem = new_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        new_signer = ReleaseSigner.from_private_pem("new-key", new_private_pem)
        unknown_private = ec.generate_private_key(ec.SECP256R1())
        unknown_private_pem = unknown_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        unknown_signer = ReleaseSigner.from_private_pem("unknown-key", unknown_private_pem)

        def spki_hex(signer: ReleaseSigner) -> str:
            public_key = serialization.load_pem_public_key(signer.public_key_pem())
            return public_key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).hex()

        trusted = {"old-key": spki_hex(old_signer), "new-key": spki_hex(new_signer)}
        root = {
            "protocol_version": 2,
            "created_at": "2026-08-22T00:00:00Z",
            "current_contract_major": 1,
            "minimum_supported_contract_major": 1,
            "contracts": {
                "1": {
                    "dataset_id": "sha256:" + "1" * 64,
                    "target": {"sha256": "2" * 64, "size_bytes": 10},
                    "full": {
                        "key": "reference/v2/contracts/1/full/" + "2" * 64 + ".sqlite.gz",
                        "compression": "gzip",
                        "sha256": "3" * 64,
                        "size_bytes": 5,
                    },
                    "patches": [],
                    "history": [],
                }
            },
        }
        payload = (json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n").encode()

        for signer in (old_signer, new_signer):
            raw = encode_signed_envelope(signer.sign_payload(payload, release_sequence=42))
            self.assertEqual(
                module.verify_root(raw, contract_major=1, trusted_public_keys=trusted)[
                    "release_sequence"
                ],
                42,
            )

        unknown = encode_signed_envelope(unknown_signer.sign_payload(payload, release_sequence=42))
        with self.assertRaisesRegex(ValueError, "untrusted key"):
            module.verify_root(unknown, contract_major=1, trusted_public_keys=trusted)


if __name__ == "__main__":
    unittest.main()
