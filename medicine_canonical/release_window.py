from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .reference_contracts.registry import implementation_for, supported_contract_majors
from .release import compress_snapshot
from .release_r2 import (
    IMMUTABLE_CACHE_CONTROL,
    LATEST_CACHE_CONTROL,
    _head_optional,
    _precondition_failed,
    _put_immutable,
    _read_body_bytes,
    _sha256_bytes,
    _verify_head,
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
    load_candidate as _load_candidate,
    patch_prefix as _patch_prefix,
    prepare_contract as _prepare_contract,
)
from .release_window_protocol import (
    MAX_ACTIVE_CONTRACTS,
    validate_root_shape as _validate_root_shape,
    validate_window as _validate_window,
)


PROTOCOL_VERSION = 2
ROOT_KEY = f"{RELEASE_PREFIX}/latest.json"


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


def _ensure_current_full_artifact(
    client,
    bucket: str,
    *,
    major: int,
    entry: dict,
    candidate: _CandidateMetadata,
    output_dir: Path,
) -> None:
    full = entry["full"]
    key = str(full["key"])
    existing = _head_optional(client, bucket, key)
    if existing is not None:
        _verify_head(
            existing,
            size_bytes=int(full["size_bytes"]),
            sha256=str(full["sha256"]),
            key=key,
        )
        return

    repair_dir = output_dir / ".repair"
    repair_dir.mkdir(parents=True, exist_ok=True)
    repair = repair_dir / f"contract-{major}-{candidate.target_sha256}.sqlite.gz"
    compressed = compress_snapshot(candidate.candidate.database, repair)
    if (
        compressed["sha256"] != full["sha256"]
        or compressed["size_bytes"] != full["size_bytes"]
    ):
        repair.unlink(missing_ok=True)
        raise RuntimeError(
            f"cannot repair contract {major} full artifact from unchanged signed target"
        )
    try:
        _put_immutable(
            client,
            bucket,
            key,
            repair,
            content_type="application/gzip",
        )
    finally:
        repair.unlink(missing_ok=True)


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
    allow_early_retirement: bool = False,
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
        allow_early_retirement=allow_early_retirement,
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
    root_dir = Path(output_dir)
    root_dir.mkdir(parents=True, exist_ok=True)

    if _root_matches_candidates(
        previous_root,
        by_major,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
    ):
        assert initial_raw is not None and previous_root is not None and previous_sequence is not None
        for major, candidate in sorted(by_major.items()):
            _ensure_current_full_artifact(
                client,
                bucket,
                major=major,
                entry=previous_root["contracts"][str(major)],
                candidate=candidate,
                output_dir=root_dir,
            )
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

    # A root update can reuse one contract target while another contract changes
    # (for example C1 unchanged when C2 is introduced). Verify every mandatory
    # active full against authoritative object state before signing the new root;
    # otherwise a missing reused N-1 artifact could be re-advertised indefinitely.
    for major, candidate in sorted(by_major.items()):
        _ensure_current_full_artifact(
            client,
            bucket,
            major=major,
            entry=prepared[major].entry,
            candidate=candidate,
            output_dir=root_dir,
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
        "skipped_patch_bases": {
            str(major): contract.skipped_bases
            for major, contract in sorted(prepared.items())
            if contract.skipped_bases
        },
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
    implementation = implementation_for(major)
    return publish_contract_window(
        client_from_env(),
        bucket,
        [
            ContractReleaseCandidate(
                major,
                target_db,
                manifest_path,
                verifier=implementation.verify,
            )
        ],
        output_dir,
        signer=release_signer_from_env(),
        release_sequence=release_sequence_from_env(),
        current_contract_major=major,
        minimum_supported_contract_major=major,
        created_at=created_at,
    )


def publish_contract_directory_from_env(
    contract_dir: str | Path,
    output_dir: str | Path,
    *,
    created_at: str | None = None,
    retire_previous_contract: bool = False,
) -> dict:
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    root = Path(contract_dir)
    majors = supported_contract_majors()
    client = client_from_env()
    signer = release_signer_from_env()
    retirement_active = False
    if not retire_previous_contract and len(majors) == 2:
        _, _, published_root, _ = _read_root(
            client,
            bucket,
            trusted_public_keys={signer.key_id: signer.public_key_pem()},
        )
        retirement_active = bool(
            published_root
            and published_root.get("current_contract_major") == majors[-1]
            and published_root.get("minimum_supported_contract_major") == majors[-1]
        )
    effective_retirement = retire_previous_contract or retirement_active
    selected_majors = majors
    minimum_supported = majors[0]
    if effective_retirement:
        if len(majors) != 2 or majors[-1] <= 1:
            raise ValueError("previous-contract retirement requires an N/N-1 contract window")
        selected_majors = (majors[-1],)
        minimum_supported = majors[-1]
    candidates = [
        ContractReleaseCandidate(
            major,
            root / f"contract-{major}.sqlite",
            root / f"contract-{major}.manifest.json",
            verifier=implementation_for(major).verify,
        )
        for major in selected_majors
    ]
    return publish_contract_window(
        client,
        bucket,
        candidates,
        output_dir,
        signer=signer,
        release_sequence=release_sequence_from_env(),
        current_contract_major=majors[-1],
        minimum_supported_contract_major=minimum_supported,
        created_at=created_at,
        allow_early_retirement=effective_retirement,
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
    "publish_contract_directory_from_env",
    "publish_contract_window_from_env",
]