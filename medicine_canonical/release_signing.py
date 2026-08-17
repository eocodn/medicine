from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


RELEASE_SIGNATURE_ENVELOPE_VERSION = 1
RELEASE_SIGNATURE_ALGORITHM = "ECDSA_P256_SHA256"
_SIGNATURE_MAGIC = b"MEDREFSIG1"
_SIGNATURE_FRAME = struct.Struct(">IQQ")
_KEY_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_MAX_SEQUENCE = (1 << 63) - 1
_KMS_KEY_VERSION_RE = re.compile(
    r"projects/[^/\s]+/locations/[^/\s]+/keyRings/[^/\s]+/"
    r"cryptoKeys/[^/\s]+/cryptoKeyVersions/[1-9][0-9]*\Z"
)
_ENVELOPE_FIELDS = {
    "envelope_version",
    "algorithm",
    "key_id",
    "release_sequence",
    "payload_base64",
    "signature_base64",
}


def _validate_key_id(key_id: str) -> str:
    if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
        raise ValueError("key_id must be 1-64 ASCII letters, digits, '.', '_' or '-'")
    return key_id


def _validate_release_sequence(release_sequence: int) -> int:
    if (
        not isinstance(release_sequence, int)
        or isinstance(release_sequence, bool)
        or release_sequence <= 0
        or release_sequence > _MAX_SEQUENCE
    ):
        raise ValueError("release_sequence must be a positive signed 64-bit integer")
    return release_sequence


def _signing_message(key_id: str, release_sequence: int, payload: bytes) -> bytes:
    key_bytes = _validate_key_id(key_id).encode("ascii")
    sequence = _validate_release_sequence(release_sequence)
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("release manifest payload must be non-empty bytes")
    # This framing is part of the cross-language trust contract with Android.
    # Keep the sequence inside the signed bytes so it cannot be rewritten to
    # replay an older, otherwise-valid manifest.
    return b"".join(
        (
            _SIGNATURE_MAGIC,
            _SIGNATURE_FRAME.pack(len(key_bytes), sequence, len(payload)),
            key_bytes,
            payload,
        )
    )


def _load_p256_public_key(public_key_pem: bytes):
    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid release signing public key PEM") from exc
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise ValueError("release signing public key must use ECDSA P-256")
    return public_key


@dataclass(frozen=True)
class ReleaseSigner:
    key_id: str
    private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def from_private_pem(cls, key_id: str, private_key_pem: bytes) -> "ReleaseSigner":
        key_id = _validate_key_id(key_id)
        try:
            private_key = serialization.load_pem_private_key(private_key_pem, password=None)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid release signing private key PEM") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise ValueError("release signing private key must use ECDSA P-256")
        return cls(key_id=key_id, private_key=private_key)

    def public_key_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign_payload(self, payload: bytes, *, release_sequence: int) -> dict:
        message = _signing_message(self.key_id, release_sequence, payload)
        signature = self.private_key.sign(
            message,
            ec.ECDSA(hashes.SHA256(), deterministic_signing=True),
        )
        return {
            "envelope_version": RELEASE_SIGNATURE_ENVELOPE_VERSION,
            "algorithm": RELEASE_SIGNATURE_ALGORITHM,
            "key_id": self.key_id,
            "release_sequence": release_sequence,
            "payload_base64": base64.b64encode(payload).decode("ascii"),
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }


@dataclass(frozen=True)
class KmsReleaseSigner:
    key_id: str
    key_version: str
    client: object
    public_key: ec.EllipticCurvePublicKey

    @classmethod
    def from_client(cls, *, key_id: str, key_version: str, client: object) -> "KmsReleaseSigner":
        key_id = _validate_key_id(key_id)
        if not isinstance(key_version, str) or not _KMS_KEY_VERSION_RE.fullmatch(key_version):
            raise ValueError("release signing KMS key version resource name is invalid")
        try:
            response = client.get_public_key(request={"name": key_version})
        except Exception as exc:
            raise RuntimeError("failed to fetch release signing KMS public key") from exc
        pem = getattr(response, "pem", None)
        if not isinstance(pem, str) or not pem.strip():
            raise ValueError("release signing KMS public key is missing")
        public_key = _load_p256_public_key(pem.encode("ascii"))
        return cls(key_id=key_id, key_version=key_version, client=client, public_key=public_key)

    def public_key_pem(self) -> bytes:
        return self.public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign_payload(self, payload: bytes, *, release_sequence: int) -> dict:
        message = _signing_message(self.key_id, release_sequence, payload)
        digest = hashlib.sha256(message).digest()
        try:
            response = self.client.asymmetric_sign(
                request={"name": self.key_version, "digest": {"sha256": digest}}
            )
        except Exception as exc:
            raise RuntimeError("release signing KMS request failed") from exc
        signature = getattr(response, "signature", None)
        if not isinstance(signature, bytes) or not signature:
            raise RuntimeError("release signing KMS returned an invalid signature")
        try:
            self.public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise RuntimeError("release signing KMS signature failed local verification") from exc
        return {
            "envelope_version": RELEASE_SIGNATURE_ENVELOPE_VERSION,
            "algorithm": RELEASE_SIGNATURE_ALGORITHM,
            "key_id": self.key_id,
            "release_sequence": release_sequence,
            "payload_base64": base64.b64encode(payload).decode("ascii"),
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        }


def encode_signed_envelope(envelope: dict) -> bytes:
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
        raise ValueError("signed release envelope fields are invalid")
    return (
        json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def verify_signed_envelope(
    raw: bytes,
    trusted_public_keys: dict[str, bytes],
    *,
    minimum_release_sequence: int | None = None,
) -> dict:
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed release envelope is invalid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
        raise ValueError("signed release envelope fields are invalid")
    if envelope.get("envelope_version") != RELEASE_SIGNATURE_ENVELOPE_VERSION:
        raise ValueError("signed release envelope version is unsupported")
    if envelope.get("algorithm") != RELEASE_SIGNATURE_ALGORITHM:
        raise ValueError("signed release algorithm is unsupported")

    key_id = _validate_key_id(envelope.get("key_id"))
    release_sequence = _validate_release_sequence(envelope.get("release_sequence"))
    if minimum_release_sequence is not None:
        minimum = _validate_release_sequence(minimum_release_sequence)
        if release_sequence <= minimum:
            raise ValueError("release sequence is not newer than the accepted sequence")
    public_key_pem = trusted_public_keys.get(key_id)
    if public_key_pem is None:
        raise ValueError(f"untrusted release signing key: {key_id}")

    try:
        payload = base64.b64decode(envelope["payload_base64"], validate=True)
        signature = base64.b64decode(envelope["signature_base64"], validate=True)
    except (binascii.Error, TypeError) as exc:
        raise ValueError("signed release envelope contains invalid base64") from exc
    if not payload or not signature:
        raise ValueError("signed release envelope payload and signature are required")

    public_key = _load_p256_public_key(public_key_pem)
    message = _signing_message(key_id, release_sequence, payload)
    try:
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("release manifest signature is invalid") from exc

    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed release payload is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("signed release payload schema is unsupported")
    return {
        "key_id": key_id,
        "release_sequence": release_sequence,
        "payload_bytes": payload,
        "manifest": manifest,
    }


__all__ = [
    "KmsReleaseSigner",
    "RELEASE_SIGNATURE_ALGORITHM",
    "RELEASE_SIGNATURE_ENVELOPE_VERSION",
    "ReleaseSigner",
    "encode_signed_envelope",
    "verify_signed_envelope",
]