from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .release_r2 import (
    IMMUTABLE_CACHE_CONTROL,
    LATEST_CACHE_CONTROL,
    _head_optional,
    _precondition_failed,
    _put_immutable,
    _read_body_bytes,
    _sha256_bytes,
    client_from_env,
)
from .release_signing import (
    KmsReleaseSigner,
    ReleaseSigner,
    encode_signed_envelope,
    verify_signed_envelope,
)
from .release_signing_runtime import release_sequence_from_env, release_signer_from_env
from .release_window_artifacts import (
    CandidateMetadata as _CandidateMetadata,
    ContractReleaseCandidate,
    FULL_SNAPSHOT_RETENTION,
    MAX_PATCH_BASES,
    PreparedContract as _PreparedContract,
    RELEASE_PREFIX,
    contract_inventory as _contract_inventory,
    entry_keys as _entry_keys,
    full_prefix as _full_prefix,
    load_candidate as _load_candidate,
    patch_prefix as _patch_prefix,
    prepare_contract as _prepare_contract,
)


PROTOCOL_VERSION = 2
ROOT_KEY = f"{RELEASE_PREFIX}/latest.json"
MAX_ACTIVE_CONTRACTS = 2
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_window(
    metadata: list[_CandidateMetadata],
    *,
    current_contract_major: int,
    minimum_supported_contract_major: int,
) -> dict[int, _CandidateMetadata]:
    if (
        not isinstance(current_contract_major, int)
        or isinstance(current_contract_major, bool)
        or current_contract_major <= 0
    ):
        raise ValueError("current contract major must be a positive integer")
    if (
        not isinstance(minimum_supported_contract_major, int)
        or isinstance(minimum_supported_contract_major, bool)
        or minimum_supported_contract_major <= 0
    ):
        raise ValueError("minimum supported contract major must be a positive integer")
    if minimum_supported_contract_major > current_contract_major:
        raise ValueError("minimum supported contract major cannot exceed current contract major")
    expected_minimum = (
        current_contract_major
        if current_contract_major == 1
        else current_contract_major - 1
    )
    if minimum_supported_contract_major != expected_minimum:
        raise ValueError(
            "supported window must contain current N and previous N-1 contract majors"
        )
    expected = set(range(minimum_supported_contract_major, current_contract_major + 1))
    if len(expected) > MAX_ACTIVE_CONTRACTS:
        raise ValueError("supported window is limited to current N and previous N-1 contracts")
    by_major: dict[int, _CandidateMetadata] = {}
    for item in metadata:
        if item.candidate.contract_major in by_major:
            raise ValueError(f"duplicate release candidate for contract {item.candidate.contract_major}")
        by_major[item.candidate.contract_major] = item
    if set(by_major) != expected:
        raise ValueError(
            "release candidates must exactly match the supported N and N-1 contract window"
        )
    return by_major


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _read_root(
    client,
    bucket: str,
    *,
    trusted_public_keys: dict[str, bytes],
) -> tuple[bytes | None, str | None, dict | None, int | None]:
    try:
        response = client.get_object(Bucket=bucket, Key=ROOT_KEY)
    except Exception as exc:
        if _not_found(exc):
            return None, None, None, None
        raise
    raw = _read_body_bytes(response["Body"])
    verified = verify_signed_envelope(raw, trusted_public_keys)
    root = verified["manifest"]
    if not isinstance(root, dict) or root.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("remote reference root protocol is unsupported")
    _validate_root_shape(root)
    return raw, response.get("ETag"), root, verified["release_sequence"]


def _validate_root_shape(root: dict) -> None:
    current = root.get("current_contract_major")
    minimum = root.get("minimum_supported_contract_major")
    contracts = root.get("contracts")
    if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
        raise ValueError("remote reference root current contract major is invalid")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
        raise ValueError("remote reference root minimum contract major is invalid")
    expected_minimum = current if current == 1 else current - 1
    if minimum != expected_minimum:
        raise ValueError("remote reference root support window is invalid")
    if not isinstance(contracts, dict):
        raise ValueError("remote reference root contracts are invalid")
    expected = {str(major) for major in range(minimum, current + 1)}
    if set(contracts) != expected:
        raise ValueError("remote reference root contracts do not match support window")
    for major_text, entry in contracts.items():
        if not isinstance(entry, dict):
            raise ValueError(f"remote contract {major_text} entry is invalid")
        _validate_contract_entry(int(major_text), entry)


def _validate_contract_entry(contract_major: int, entry: dict) -> None:
    dataset_id = entry.get("dataset_id")
    target = entry.get("target")
    full = entry.get("full")
    patches = entry.get("patches")
    history = entry.get("history", [])
    if not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError(f"remote contract {contract_major} dataset identity is invalid")
    if not isinstance(target, dict):
        raise ValueError(f"remote contract {contract_major} target is invalid")
    if not _SHA256.fullmatch(str(target.get("sha256") or "")):
        raise ValueError(f"remote contract {contract_major} target SHA-256 is invalid")
    if not isinstance(target.get("size_bytes"), int) or target["size_bytes"] <= 0:
        raise ValueError(f"remote contract {contract_major} target size is invalid")
    if not isinstance(full, dict) or full.get("compression") != "gzip":
        raise ValueError(f"remote contract {contract_major} full snapshot is invalid")
    expected_full_prefix = _full_prefix(contract_major)
    if not isinstance(full.get("key"), str) or not full["key"].startswith(expected_full_prefix):
        raise ValueError(f"remote contract {contract_major} full snapshot key is invalid")
    if not _SHA256.fullmatch(str(full.get("sha256") or "")):
        raise ValueError(f"remote contract {contract_major} full snapshot SHA-256 is invalid")
    if not isinstance(full.get("size_bytes"), int) or full["size_bytes"] <= 0:
        raise ValueError(f"remote contract {contract_major} full snapshot size is invalid")
    if not isinstance(patches, list):
        raise ValueError(f"remote contract {contract_major} patches are invalid")
    if not isinstance(history, list) or len(history) > FULL_SNAPSHOT_RETENTION - 1:
        raise ValueError(f"remote contract {contract_major} history is invalid")


def _target_identity(entry: dict) -> tuple[str, str, int]:
    return (
        str(entry.get("dataset_id") or ""),
        str((entry.get("target") or {}).get("sha256") or ""),
        int((entry.get("target") or {}).get("size_bytes") or -1),
    )


def _candidate_identity(candidate: _CandidateMetadata) -> tuple[str, str, int]:
    return candidate.dataset_id, candidate.target_sha256, candidate.target_size_bytes


def _root_matches_candidates(
    root: dict | None,
    candidates: dict[int, _CandidateMetadata],
    *,
    current_contract_major: int,
    minimum_supported_contract_major: int,
) -> bool:
    if root is None:
        return False
    if root.get("current_contract_major") != current_contract_major:
        return False
    if root.get("minimum_supported_contract_major") != minimum_supported_contract_major:
        return False
    contracts = root.get("contracts")
    if not isinstance(contracts, dict) or set(contracts) != {str(m) for m in candidates}:
        return False
    return all(
        _target_identity(contracts[str(major)]) == _candidate_identity(candidate)
        for major, candidate in candidates.items()
    )


def _put_root(
    client,
    bucket: str,
    body: bytes,
    *,
    previous_etag: str | None,
) -> None:
    conditional = {"If-Match": previous_etag} if previous_etag else {"If-None-Match": "*"}
    try:
        client.put_object(
            Bucket=bucket,
            Key=ROOT_KEY,
            Body=body,
            ContentType="application/json",
            CacheControl=LATEST_CACHE_CONTROL,
            Metadata={"sha256": _sha256_bytes(body)},
            custom_headers=conditional,
        )
    except Exception as exc:
        if _precondition_failed(exc):
            raise RuntimeError("remote reference root changed during publication") from exc
        raise
    response = client.get_object(Bucket=bucket, Key=ROOT_KEY)
    round_trip = _read_body_bytes(response["Body"])
    if round_trip != body:
        raise RuntimeError("remote reference root does not match published body")
    if (response.get("Metadata") or {}).get("sha256") != _sha256_bytes(body):
        raise RuntimeError("remote reference root hash metadata does not match")


def _cleanup_active_contracts(
    client,
    bucket: str,
    *,
    root: dict,
    initial_inventory: dict[int, set[str]],
    expected_root_raw: bytes,
    trusted_public_keys: dict[str, bytes],
) -> dict[int, list[str]]:
    current_raw, _, current_root, _ = _read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != expected_root_raw or current_root != root:
        raise RuntimeError("remote reference root changed before retention cleanup")
    deleted: dict[int, list[str]] = {}
    for major, inventory in sorted(initial_inventory.items()):
        entry = root["contracts"][str(major)]
        keep = _entry_keys(major, entry)
        stale = sorted(inventory - keep)
        deleted[major] = []
        for key in stale:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception as exc:
                raise RuntimeError(f"reference contract retention cleanup failed deleting {key}") from exc
            if _head_optional(client, bucket, key) is not None:
                raise RuntimeError(f"reference contract retention cleanup did not delete {key}")
            deleted[major].append(key)
    return deleted


def publish_contract_window(
    client,
    bucket: str,
    candidates: list[ContractReleaseCandidate],
    output_dir: str | Path,
    *,
    signer: ReleaseSigner | KmsReleaseSigner,
    release_sequence: int,
    current_contract_major: int,
    minimum_supported_contract_major: int,
    created_at: str | None = None,
) -> dict:
    if not str(bucket).strip():
        raise ValueError("R2 bucket is required")
    if (
        not isinstance(release_sequence, int)
        or isinstance(release_sequence, bool)
        or release_sequence <= 0
        or release_sequence > (1 << 63) - 1
    ):
        raise ValueError("release_sequence must be a positive signed 64-bit integer")
    metadata = [_load_candidate(candidate) for candidate in candidates]
    by_major = _validate_window(
        metadata,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
    )
    trusted_public_keys = {signer.key_id: signer.public_key_pem()}
    initial_raw, initial_etag, previous_root, previous_sequence = _read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    if previous_root is not None:
        previous_current = int(previous_root["current_contract_major"])
        previous_minimum = int(previous_root["minimum_supported_contract_major"])
        if (
            current_contract_major < previous_current
            or minimum_supported_contract_major < previous_minimum
        ):
            raise ValueError("reference contract support window cannot move backward")
    initial_inventory = {
        major: _contract_inventory(client, bucket, major)
        for major in by_major
    }

    if _root_matches_candidates(
        previous_root,
        by_major,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
    ):
        assert initial_raw is not None and previous_root is not None and previous_sequence is not None
        cleanup = _cleanup_active_contracts(
            client,
            bucket,
            root=previous_root,
            initial_inventory=initial_inventory,
            expected_root_raw=initial_raw,
            trusted_public_keys=trusted_public_keys,
        )
        return {
            "status": "unchanged",
            "release_sequence": previous_sequence,
            "root": previous_root,
            "cleanup": cleanup,
        }

    if previous_sequence is not None and release_sequence <= previous_sequence:
        raise ValueError("release_sequence must be greater than the published reference root sequence")

    root_dir = Path(output_dir)
    root_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[int, _PreparedContract] = {}
    previous_contracts = (previous_root or {}).get("contracts") or {}
    with tempfile.TemporaryDirectory(dir=root_dir, prefix="reference-window-") as temporary:
        temporary_root = Path(temporary)
        for major, candidate in sorted(by_major.items()):
            previous_entry = previous_contracts.get(str(major))
            if (
                isinstance(previous_entry, dict)
                and _target_identity(previous_entry) == _candidate_identity(candidate)
            ):
                prepared[major] = _PreparedContract(
                    entry=dict(previous_entry),
                    full_path=None,
                    patch_paths={},
                )
                continue
            prepared[major] = _prepare_contract(
                client,
                bucket,
                candidate,
                previous_entry if isinstance(previous_entry, dict) else None,
                root_dir,
                temporary_root,
            )

    for major, contract in sorted(prepared.items()):
        if contract.full_path is not None:
            full = contract.entry["full"]
            _put_immutable(
                client,
                bucket,
                full["key"],
                contract.full_path,
                content_type="application/gzip",
            )
        for patch in contract.entry["patches"]:
            path = contract.patch_paths.get(patch["key"])
            if path is not None:
                _put_immutable(
                    client,
                    bucket,
                    patch["key"],
                    path,
                    content_type="application/octet-stream",
                )

    current_raw, current_etag, _, _ = _read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != initial_raw or current_etag != initial_etag:
        raise RuntimeError("remote reference root changed during publication")

    root = {
        "protocol_version": PROTOCOL_VERSION,
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "current_contract_major": current_contract_major,
        "minimum_supported_contract_major": minimum_supported_contract_major,
        "contracts": {
            str(major): prepared[major].entry
            for major in sorted(prepared)
        },
    }
    payload = (
        json.dumps(root, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    body = encode_signed_envelope(signer.sign_payload(payload, release_sequence=release_sequence))
    _put_root(client, bucket, body, previous_etag=initial_etag)
    cleanup = _cleanup_active_contracts(
        client,
        bucket,
        root=root,
        initial_inventory=initial_inventory,
        expected_root_raw=body,
        trusted_public_keys=trusted_public_keys,
    )
    return {
        "status": "published",
        "release_sequence": release_sequence,
        "root": root,
        "cleanup": cleanup,
    }


def publish_contract_window_from_env(
    target_db: str | Path,
    mobile_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    created_at: str | None = None,
) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    manifest_path = Path(mobile_manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("reference contract manifest is invalid JSON") from exc
    major = manifest.get("contract_major") if isinstance(manifest, dict) else None
    if not isinstance(major, int) or isinstance(major, bool) or major <= 0:
        raise ValueError("reference contract manifest major is invalid")
    # Contract 1 has no predecessor.  When contract 2 is introduced the publish
    # workflow must supply both exporters through `publish_contract_window` so a
    # supported predecessor is never silently dropped.
    return publish_contract_window(
        client_from_env(),
        bucket,
        [ContractReleaseCandidate(major, target_db, manifest_path)],
        output_dir,
        signer=release_signer_from_env(),
        release_sequence=release_sequence_from_env(),
        current_contract_major=major,
        minimum_supported_contract_major=major,
        created_at=created_at,
    )


__all__ = [
    "ContractReleaseCandidate",
    "FULL_SNAPSHOT_RETENTION",
    "MAX_ACTIVE_CONTRACTS",
    "MAX_PATCH_BASES",
    "PROTOCOL_VERSION",
    "RELEASE_PREFIX",
    "ROOT_KEY",
    "publish_contract_window",
    "publish_contract_window_from_env",
]