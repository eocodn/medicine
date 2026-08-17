from __future__ import annotations

import os

from .release_signing import KmsReleaseSigner


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


__all__ = ["release_sequence_from_env", "release_signer_from_env"]