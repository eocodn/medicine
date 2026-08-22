from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from .release_signing import _load_p256_public_key, _validate_key_id


MAX_TRUST_MANIFEST_BYTES = 64 * 1024
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class TrustedSigningKey:
    key_id: str
    public_key_pem: bytes
    public_key_spki: bytes
    spki_sha256: str


@dataclass(frozen=True)
class TrustedSigningManifest:
    active_key_id: str
    keys: tuple[TrustedSigningKey, ...]

    def public_keys_pem(self) -> dict[str, bytes]:
        return {key.key_id: key.public_key_pem for key in self.keys}

    def public_keys_spki_hex(self) -> dict[str, str]:
        return {key.key_id: key.public_key_spki.hex() for key in self.keys}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"trusted release signing key file has duplicate field: {key}")
        result[key] = value
    return result


def load_trusted_signing_manifest(path: str | Path) -> TrustedSigningManifest:
    manifest_path = Path(path)
    try:
        if not manifest_path.is_file():
            raise RuntimeError("trusted release signing key file is not a regular file")
        if manifest_path.stat().st_size > MAX_TRUST_MANIFEST_BYTES:
            raise RuntimeError("trusted release signing key file is too large")
        document = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("trusted release signing key file is invalid") from exc

    if not isinstance(document, dict) or set(document) != {"active_key_id", "keys"}:
        raise RuntimeError("trusted release signing key file shape is invalid")
    active_key_id = document.get("active_key_id")
    try:
        active_key_id = _validate_key_id(active_key_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("trusted release signing active key ID is invalid") from exc

    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise RuntimeError("trusted release signing keys must be a non-empty list")

    keys: list[TrustedSigningKey] = []
    seen: set[str] = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict) or set(raw_key) != {
            "key_id",
            "public_key_pem",
            "spki_sha256",
        }:
            raise RuntimeError("trusted release signing key entry shape is invalid")
        try:
            key_id = _validate_key_id(raw_key.get("key_id"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("trusted release signing key ID is invalid") from exc
        if key_id in seen:
            raise RuntimeError(f"trusted release signing key ID is duplicated: {key_id}")
        seen.add(key_id)

        public_key_text = raw_key.get("public_key_pem")
        fingerprint = raw_key.get("spki_sha256")
        if not isinstance(public_key_text, str):
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}")
        if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise RuntimeError(f"trusted release signing fingerprint is invalid: {key_id}")
        try:
            public_key_text.encode("ascii")
            public_key = _load_p256_public_key(public_key_text.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise RuntimeError(f"trusted release signing public key is invalid: {key_id}") from exc
        spki = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if hashlib.sha256(spki).hexdigest() != fingerprint:
            raise RuntimeError(f"trusted release signing fingerprint does not match: {key_id}")
        canonical_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        keys.append(
            TrustedSigningKey(
                key_id=key_id,
                public_key_pem=canonical_pem,
                public_key_spki=spki,
                spki_sha256=fingerprint,
            )
        )

    if active_key_id not in seen:
        raise RuntimeError("active signer key ID is missing from trusted release signing keys")
    return TrustedSigningManifest(active_key_id=active_key_id, keys=tuple(keys))


__all__ = [
    "MAX_TRUST_MANIFEST_BYTES",
    "TrustedSigningKey",
    "TrustedSigningManifest",
    "load_trusted_signing_manifest",
]
