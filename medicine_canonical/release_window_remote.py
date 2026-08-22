from __future__ import annotations

from pathlib import Path

from .release import compress_snapshot
from .release_r2 import (
    LATEST_CACHE_CONTROL,
    _head_optional,
    _precondition_failed,
    _put_immutable,
    _read_body_bytes,
    _sha256_bytes,
    _verify_head,
)
from .release_signing import verify_signed_envelope
from .release_window_artifacts import (
    CandidateMetadata,
    RELEASE_PREFIX,
    entry_keys,
)
from .release_window_protocol import validate_root_shape


PROTOCOL_VERSION = 2
ROOT_KEY = f"{RELEASE_PREFIX}/latest.json"


def _not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    code = response.get("Error", {}).get("Code")
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def read_root(
    client,
    bucket: str,
    *,
    trusted_public_keys: dict[str, bytes],
) -> tuple[bytes | None, str | None, dict | None, int | None, dict | None]:
    try:
        response = client.get_object(Bucket=bucket, Key=ROOT_KEY)
    except Exception as exc:
        if _not_found(exc):
            return None, None, None, None, None
        raise
    raw = _read_body_bytes(response["Body"])
    verified = verify_signed_envelope(raw, trusted_public_keys)
    root = verified["manifest"]
    if not isinstance(root, dict) or root.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("remote reference root protocol is unsupported")
    validate_root_shape(root)
    return raw, response.get("ETag"), root, verified["release_sequence"], verified


def target_identity(entry: dict) -> tuple[str, str, int]:
    return (
        str(entry.get("dataset_id") or ""),
        str((entry.get("target") or {}).get("sha256") or ""),
        int((entry.get("target") or {}).get("size_bytes") or -1),
    )


def candidate_identity(candidate: CandidateMetadata) -> tuple[str, str, int]:
    return candidate.dataset_id, candidate.target_sha256, candidate.target_size_bytes


def root_matches_candidates(
    root: dict | None,
    candidates: dict[int, CandidateMetadata],
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
        target_identity(contracts[str(major)]) == candidate_identity(candidate)
        for major, candidate in candidates.items()
    )


def validate_support_window_progression(
    previous_root: dict | None,
    *,
    current_contract_major: int,
    minimum_supported_contract_major: int,
) -> None:
    if previous_root is None:
        return
    previous_current = int(previous_root["current_contract_major"])
    previous_minimum = int(previous_root["minimum_supported_contract_major"])
    if (
        current_contract_major < previous_current
        or minimum_supported_contract_major < previous_minimum
    ):
        raise ValueError("reference contract support window cannot move backward")


def ensure_current_full_artifact(
    client,
    bucket: str,
    *,
    major: int,
    entry: dict,
    candidate: CandidateMetadata,
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
    if compressed["sha256"] != full["sha256"] or compressed["size_bytes"] != full["size_bytes"]:
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


def ensure_window_full_artifacts(
    client,
    bucket: str,
    *,
    candidates: dict[int, CandidateMetadata],
    entries: dict[int, dict],
    output_dir: Path,
) -> None:
    for major, candidate in sorted(candidates.items()):
        ensure_current_full_artifact(
            client,
            bucket,
            major=major,
            entry=entries[major],
            candidate=candidate,
            output_dir=output_dir,
        )


def put_root(client, bucket: str, body: bytes, *, previous_etag: str | None) -> None:
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


def cleanup_active_contracts(
    client,
    bucket: str,
    *,
    root: dict,
    initial_inventory: dict[int, set[str]],
    expected_root_raw: bytes,
    trusted_public_keys: dict[str, bytes],
) -> dict[int, list[str]]:
    current_raw, _, current_root, _, _ = read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != expected_root_raw or current_root != root:
        raise RuntimeError("remote reference root changed before retention cleanup")
    deleted: dict[int, list[str]] = {}
    for major, inventory in sorted(initial_inventory.items()):
        keep = entry_keys(major, root["contracts"][str(major)])
        stale = sorted(inventory - keep)
        deleted[major] = []
        for key in stale:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception as exc:
                raise RuntimeError(
                    f"reference contract retention cleanup failed deleting {key}"
                ) from exc
            if _head_optional(client, bucket, key) is not None:
                raise RuntimeError(f"reference contract retention cleanup did not delete {key}")
            deleted[major].append(key)
    return deleted


def unchanged_publication_result(
    client,
    bucket: str,
    *,
    previous_raw: bytes | None,
    previous_root: dict | None,
    previous_sequence: int | None,
    candidates: dict[int, CandidateMetadata],
    current_contract_major: int,
    minimum_supported_contract_major: int,
    initial_inventory: dict[int, set[str]],
    trusted_public_keys: dict[str, bytes],
    output_dir: Path,
) -> dict | None:
    if not root_matches_candidates(
        previous_root,
        candidates,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
    ):
        return None
    assert previous_raw is not None and previous_root is not None and previous_sequence is not None
    entries = {
        major: previous_root["contracts"][str(major)]
        for major in candidates
    }
    ensure_window_full_artifacts(
        client,
        bucket,
        candidates=candidates,
        entries=entries,
        output_dir=output_dir,
    )
    cleanup = cleanup_active_contracts(
        client,
        bucket,
        root=previous_root,
        initial_inventory=initial_inventory,
        expected_root_raw=previous_raw,
        trusted_public_keys=trusted_public_keys,
    )
    return {
        "status": "unchanged",
        "release_sequence": previous_sequence,
        "root": previous_root,
        "cleanup": cleanup,
    }


def assert_root_unchanged(
    client,
    bucket: str,
    *,
    initial_raw: bytes | None,
    initial_etag: str | None,
    trusted_public_keys: dict[str, bytes],
) -> None:
    current_raw, current_etag, _, _, _ = read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    if current_raw != initial_raw or current_etag != initial_etag:
        raise RuntimeError("remote reference root changed during publication")


__all__ = [
    "PROTOCOL_VERSION",
    "ROOT_KEY",
    "assert_root_unchanged",
    "candidate_identity",
    "ensure_window_full_artifacts",
    "put_root",
    "read_root",
    "target_identity",
    "unchanged_publication_result",
    "validate_support_window_progression",
]
