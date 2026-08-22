from __future__ import annotations

import os
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from .release_signing import KmsReleaseSigner, _load_p256_public_key, _validate_key_id

_MAX_TRUSTED_KEYS_FILE_BYTES = 64 * 1024


def _canonical_public_key(public_key_pem: bytes) -> bytes:
    public_key = _load_p256_public_key(public_key_pem)
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def validate_trusted_public_keys(*, signer, trusted_public_keys: dict[str, bytes]) -> dict[str, bytes]:
    """Validate the authoritative key set and bind it to the active signer.

    The active signer is deliberately not used to construct this set. A caller
    must provide every key that is still trusted during a rotation; omitting a
    retired key therefore revokes it at the next publication boundary.
    """
    if not isinstance(trusted_public_keys, dict) or not trusted_public_keys:
        raise RuntimeError("trusted release signing key set is required")
    normalized: dict[str, bytes] = {}
    for key_id, public_key_pem in trusted_public_keys.items():
        _validate_key_id(key_id)
        if not isinstance(public_key_pem, bytes):
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}")
        try:
            normalized[key_id] = _canonical_public_key(public_key_pem)
        except ValueError as exc:
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}") from exc

    signer_key = normalized.get(signer.key_id)
    if signer_key is None:
        raise RuntimeError("active signer key ID is missing from trusted release signing keys")
    if signer_key != _canonical_public_key(signer.public_key_pem()):
        raise RuntimeError("active signer public key does not match trusted release signing key")
    return normalized


def trusted_public_keys_from_env(*, signer) -> dict[str, bytes]:
    path_value = os.environ.get("REFERENCE_SIGNING_TRUSTED_KEYS_FILE", "").strip()
    if not path_value:
        raise RuntimeError("REFERENCE_SIGNING_TRUSTED_KEYS_FILE is required")
    path = Path(path_value)
    try:
        if not path.is_file():
            raise RuntimeError("trusted release signing key file is not a regular file")
        if path.stat().st_size > _MAX_TRUSTED_KEYS_FILE_BYTES:
            raise RuntimeError("trusted release signing key file is too large")
        document = json.loads(path.read_text(encoding="utf-8"))
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("trusted release signing key file is invalid") from exc
    if not isinstance(document, dict) or set(document) != {"active_key_id", "keys"}:
        raise RuntimeError("trusted release signing key file shape is invalid")
    active_key_id = document.get("active_key_id")
    keys = document.get("keys")
    if active_key_id != signer.key_id:
        raise RuntimeError("active signer key ID does not match trusted release signing configuration")
    if not isinstance(keys, dict):
        raise RuntimeError("trusted release signing keys must be an object")
    parsed: dict[str, bytes] = {}
    for key_id, public_key_pem in keys.items():
        if not isinstance(public_key_pem, str):
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}")
        try:
            parsed[key_id] = public_key_pem.encode("ascii")
        except UnicodeEncodeError as exc:
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}") from exc
    try:
        return validate_trusted_public_keys(signer=signer, trusted_public_keys=parsed)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("trusted release signing key file contains invalid key IDs") from exc


def release_signer_from_env(*, kms_client=None) -> KmsReleaseSigner:
    key_id = os.environ.get("REFERENCE_SIGNING_KEY_ID", "").strip()
    key_version = os.environ.get("REFERENCE_SIGNING_KMS_KEY_VERSION", "").strip()
    missing = []
    if not key_id:
        missing.append("REFERENCE_SIGNING_KEY_ID")
    if not key_version:
        missing.append("REFERENCE_SIGNING_KMS_KEY_VERSION")
    if missing:
        raise RuntimeError(f"missing release signing environment: {', '.join(missing)}")
    if kms_client is None:
        try:
            from google.cloud import kms_v1
        except ImportError as exc:
            raise RuntimeError("google-cloud-kms is required for release signing") from exc
        kms_client = kms_v1.KeyManagementServiceClient()
    return KmsReleaseSigner.from_client(
        key_id=key_id,
        key_version=key_version,
        client=kms_client,
    )


def release_sequence_from_env() -> int:
    raw = os.environ.get("REFERENCE_RELEASE_SEQUENCE", "").strip()
    if not raw:
        raise RuntimeError("REFERENCE_RELEASE_SEQUENCE is required")
    try:
        sequence = int(raw, 10)
    except ValueError as exc:
        raise RuntimeError("REFERENCE_RELEASE_SEQUENCE must be an integer") from exc
    if sequence <= 0 or sequence > (1 << 63) - 1:
        raise RuntimeError("REFERENCE_RELEASE_SEQUENCE must be a positive signed 64-bit integer")
    return sequence


__all__ = [
    "release_sequence_from_env",
    "release_signer_from_env",
    "trusted_public_keys_from_env",
    "validate_trusted_public_keys",
]
