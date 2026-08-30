#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medicine_canonical.release_trust import load_trusted_signing_manifest


SIGNATURE_MAGIC = b"MEDREFSIG1"
ENVELOPE_VERSION = 1
ALGORITHM = "ECDSA_P256_SHA256"
PROTOCOL_VERSION = 2
PATCH_FORMAT = "medicine-chunk-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
DATASET_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_SIGNED_LONG = (1 << 63) - 1


def _android_contract_major() -> int:
    source = (ROOT / "android/app/src/main/java/com/medicine/android/ReferenceRuntimeAdapters.kt").read_text()
    match = re.search(r"const val CONTRACT_MAJOR\s*=\s*([1-9][0-9]*)", source)
    if match is None:
        raise RuntimeError("cannot resolve Android Reference Contract major")
    return int(match.group(1))


def _trusted_public_keys_from_manifest(path: Path) -> dict[str, str]:
    return load_trusted_signing_manifest(path).public_keys_spki_hex()


def _decode_base64(value: object, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"signed root {label} is missing")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"signed root {label} is invalid base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"signed root {label} is non-canonical base64")
    return decoded


def _positive_int(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > MAX_SIGNED_LONG
    ):
        raise ValueError(f"signed reference root {label} is invalid")
    return value


def _sha256(value: object, label: str) -> str:
    text = str(value or "")
    if not SHA256_RE.fullmatch(text):
        raise ValueError(f"signed reference root {label} is invalid")
    return text


def _validate_contract_entry(entry: dict, contract_major: int) -> None:
    dataset_id = entry.get("dataset_id")
    if not isinstance(dataset_id, str) or not DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError("signed reference root dataset identity is invalid")

    target = entry.get("target")
    full = entry.get("full")
    patches = entry.get("patches")
    if not isinstance(target, dict) or not isinstance(full, dict) or not isinstance(patches, list):
        raise ValueError("signed reference root contract entry is incomplete")
    target_sha = _sha256(target.get("sha256"), "target SHA-256")
    _positive_int(target.get("size_bytes"), "target size")
    if full.get("compression") != "gzip":
        raise ValueError("signed reference root full compression is unsupported")
    full_key = full.get("key")
    expected_full_key = f"reference/v2/contracts/{contract_major}/full/{target_sha}.sqlite.gz"
    if full_key != expected_full_key:
        raise ValueError("signed reference root full artifact key is invalid")
    _sha256(full.get("sha256"), "full artifact SHA-256")
    full_size = _positive_int(full.get("size_bytes"), "full artifact size")

    seen_sources: set[str] = set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError("signed reference root patch entry is invalid")
        # Shipping Android intentionally ignores unknown patch formats and keeps
        # full gzip as the compatibility fallback. Match that behavior exactly.
        if str(patch.get("format") or "") != PATCH_FORMAT:
            continue
        source_sha = _sha256(patch.get("from_sha256"), "patch source SHA-256")
        _positive_int(patch.get("from_size_bytes"), "patch source size")
        _sha256(patch.get("sha256"), "patch SHA-256")
        patch_size = _positive_int(patch.get("size_bytes"), "patch size")
        expected_patch_key = (
            f"reference/v2/contracts/{contract_major}/patch/{source_sha}-{target_sha}.mpatch"
        )
        if patch.get("key") != expected_patch_key:
            raise ValueError("signed reference root patch artifact key is invalid")
        if patch_size >= full_size:
            raise ValueError("signed reference root patch is not smaller than full artifact")
        if source_sha in seen_sources:
            raise ValueError("signed reference root contains duplicate patch sources")
        seen_sources.add(source_sha)


def verify_root(
    raw: bytes,
    *,
    contract_major: int,
    trusted_public_keys: dict[str, str],
) -> dict:
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed reference root is invalid JSON") from exc
    if not isinstance(envelope, dict):
        raise ValueError("signed reference root envelope must be an object")
    if envelope.get("envelope_version") != ENVELOPE_VERSION:
        raise ValueError("signed reference root envelope version is unsupported")
    if envelope.get("algorithm") != ALGORITHM:
        raise ValueError("signed reference root algorithm is unsupported")
    key_id = envelope.get("key_id")
    if not isinstance(key_id, str):
        raise ValueError("signed reference root key ID is invalid")
    public_key_der_hex = trusted_public_keys.get(key_id)
    if public_key_der_hex is None:
        raise ValueError("signed reference root uses an untrusted key")
    sequence = envelope.get("release_sequence")
    sequence = _positive_int(sequence, "sequence")
    payload = _decode_base64(envelope.get("payload_base64"), "payload")
    signature = _decode_base64(envelope.get("signature_base64"), "signature")
    key_bytes = key_id.encode("ascii")
    signing_message = b"".join(
        (
            SIGNATURE_MAGIC,
            struct.pack(">i", len(key_bytes)),
            struct.pack(">q", sequence),
            struct.pack(">q", len(payload)),
            key_bytes,
            payload,
        )
    )
    public_key = serialization.load_der_public_key(bytes.fromhex(public_key_der_hex))
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise ValueError("reference trust anchor is not a P-256 EC public key")
    public_key.verify(signature, signing_message, ec.ECDSA(hashes.SHA256()))

    try:
        root = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("signed reference root payload is invalid JSON") from exc
    if not isinstance(root, dict) or root.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("signed reference root protocol is unsupported")
    current = root.get("current_contract_major")
    minimum = root.get("minimum_supported_contract_major")
    contracts = root.get("contracts")
    if not isinstance(contracts, dict):
        raise ValueError("signed reference root support window is invalid")
    current = _positive_int(current, "current contract major")
    minimum = _positive_int(minimum, "minimum supported contract major")
    if minimum > current or current - minimum > 1:
        raise ValueError("signed reference root support window is invalid")
    expected_contract_keys = {str(major) for major in range(minimum, current + 1)}
    if set(contracts) != expected_contract_keys:
        raise ValueError("signed reference root contracts do not match support window")
    if not (minimum <= contract_major <= current):
        raise ValueError(f"Android contract {contract_major} is not supported by the signed reference root")
    entry = contracts.get(str(contract_major))
    if not isinstance(entry, dict):
        raise ValueError(f"signed reference root omits Android contract {contract_major}")
    _validate_contract_entry(entry, contract_major)
    target = entry["target"]
    full = entry["full"]
    return {
        "contract_major": contract_major,
        "release_sequence": sequence,
        "target_sha256": target["sha256"],
        "target_size_bytes": target["size_bytes"],
        "full_key": full["key"],
        "full_sha256": full["sha256"],
        "full_size_bytes": full["size_bytes"],
    }


def verify_full_artifact(path: Path, contract: dict) -> None:
    if not path.is_file():
        raise ValueError(f"signed full artifact is missing: {path}")
    compressed_size = path.stat().st_size
    if compressed_size != contract["full_size_bytes"]:
        raise ValueError("signed full artifact size does not match reference root")
    compressed_digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            compressed_digest.update(chunk)
    compressed_sha = compressed_digest.hexdigest()
    if compressed_sha != contract["full_sha256"]:
        raise ValueError("signed full artifact SHA-256 does not match reference root")

    target_digest = hashlib.sha256()
    target_size = 0
    try:
        with gzip.open(path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                target_digest.update(chunk)
                target_size += len(chunk)
    except OSError as exc:
        raise ValueError("signed full artifact is not a valid gzip stream") from exc
    if target_size != contract["target_size_bytes"]:
        raise ValueError("signed full artifact target size does not match reference root")
    if target_digest.hexdigest() != contract["target_sha256"]:
        raise ValueError("signed full artifact target SHA-256 does not match reference root")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--contract-major", type=int)
    parser.add_argument("--key-id")
    parser.add_argument("--public-key-der-hex")
    parser.add_argument("--print-full-key", action="store_true")
    parser.add_argument("--full-artifact", type=Path)
    args = parser.parse_args(argv)
    if (args.key_id is None) != (args.public_key_der_hex is None):
        parser.error("--key-id and --public-key-der-hex must be supplied together")
    if args.key_id is not None:
        trusted_public_keys = {args.key_id: args.public_key_der_hex}
    else:
        trusted_public_keys = _trusted_public_keys_from_manifest(
            ROOT / "deploy/reference-signing-trusted-keys.json"
        )
    result = verify_root(
        args.root.read_bytes(),
        contract_major=args.contract_major or _android_contract_major(),
        trusted_public_keys=trusted_public_keys,
    )
    if args.full_artifact is not None:
        verify_full_artifact(args.full_artifact, result)
        print(
            "verified signed full reference artifact for Android contract "
            f"{result['contract_major']} at sequence {result['release_sequence']}"
        )
        return 0
    if args.print_full_key:
        print(result["full_key"])
        return 0
    print(
        "signed reference root supports Android contract "
        f"{result['contract_major']} at sequence {result['release_sequence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
