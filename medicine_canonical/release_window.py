from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .reference_contracts.registry import (
    VerifiedContractArtifact,
    build_supported_contract_artifacts,
    implementation_for,
    supported_contract_majors,
)
from .release_r2 import _put_immutable, client_from_env
from .release_signing import (
    KmsReleaseSigner,
    ReleaseSigner,
    encode_signed_envelope,
)
from .release_signing_runtime import (
    release_sequence_from_env,
    release_signer_from_env,
    trusted_public_keys_from_env,
    validate_trusted_public_keys,
)
from .release_window_artifacts import (
    CandidateMetadata as _CandidateMetadata,
    ContractReleaseCandidate,
    FULL_SNAPSHOT_RETENTION,
    MAX_PATCH_BASES,
    PreparedContract as _PreparedContract,
    RELEASE_PREFIX,
    contract_inventory as _contract_inventory,
    load_candidate as _load_candidate,
    load_verified_artifact as _load_verified_artifact,
    prepare_contract as _prepare_contract,
)
from .release_window_protocol import (
    MAX_ACTIVE_CONTRACTS,
    validate_window as _validate_window,
)
from .release_window_remote import (
    PROTOCOL_VERSION,
    ROOT_KEY,
    assert_root_unchanged as _assert_root_unchanged,
    candidate_identity as _candidate_identity,
    cleanup_active_contracts as _cleanup_active_contracts,
    ensure_window_full_artifacts as _ensure_window_full_artifacts,
    put_root as _put_root,
    read_root as _read_root,
    target_identity as _target_identity,
    unchanged_publication_result as _unchanged_publication_result,
    validate_support_window_progression as _validate_support_window_progression,
)


def _validate_release_sequence(release_sequence: int) -> None:
    if (
        not isinstance(release_sequence, int)
        or isinstance(release_sequence, bool)
        or release_sequence <= 0
        or release_sequence > (1 << 63) - 1
    ):
        raise ValueError("release_sequence must be a positive signed 64-bit integer")


def _strict_verifier_with_progress(implementation, progress):
    def verify(database, contract_major, dataset_id):
        return implementation.verify(
            database,
            contract_major,
            dataset_id,
            progress=progress,
        )

    return verify



def _prepare_contract_window(
    client,
    bucket: str,
    *,
    candidates: dict[int, _CandidateMetadata],
    previous_root: dict | None,
    output_dir: Path,
) -> dict[int, _PreparedContract]:
    prepared: dict[int, _PreparedContract] = {}
    previous_contracts = (previous_root or {}).get("contracts") or {}
    with tempfile.TemporaryDirectory(dir=output_dir, prefix="reference-window-") as temporary:
        temporary_root = Path(temporary)
        for major, candidate in sorted(candidates.items()):
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
                output_dir,
                temporary_root,
            )
    return prepared


def _upload_prepared_contracts(client, bucket: str, prepared: dict[int, _PreparedContract]) -> None:
    for _, contract in sorted(prepared.items()):
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



def _build_root(
    prepared: dict[int, _PreparedContract],
    *,
    current_contract_major: int,
    minimum_supported_contract_major: int,
    created_at: str | None,
) -> dict:
    return {
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


def _encode_root(root: dict, signer: ReleaseSigner | KmsReleaseSigner, release_sequence: int) -> bytes:
    payload = (
        json.dumps(root, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    return encode_signed_envelope(signer.sign_payload(payload, release_sequence=release_sequence))


def _publish_loaded_contract_window(
    client,
    bucket: str,
    metadata: list[_CandidateMetadata],
    output_dir: str | Path,
    *,
    signer: ReleaseSigner | KmsReleaseSigner,
    release_sequence: int,
    current_contract_major: int,
    minimum_supported_contract_major: int,
    created_at: str | None = None,
    allow_early_retirement: bool = False,
    trusted_public_keys: dict[str, bytes] | None = None,
) -> dict:
    if not str(bucket).strip():
        raise ValueError("R2 bucket is required")
    _validate_release_sequence(release_sequence)
    by_major = _validate_window(
        metadata,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
        allow_early_retirement=allow_early_retirement,
    )
    trusted_public_keys = validate_trusted_public_keys(
        signer=signer,
        trusted_public_keys=trusted_public_keys
        if trusted_public_keys is not None
        else {signer.key_id: signer.public_key_pem()},
    )
    initial_raw, initial_etag, previous_root, previous_sequence = _read_root(
        client,
        bucket,
        trusted_public_keys=trusted_public_keys,
    )
    _validate_support_window_progression(
        previous_root,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
    )
    initial_inventory = {
        major: _contract_inventory(client, bucket, major)
        for major in by_major
    }
    root_dir = Path(output_dir)
    root_dir.mkdir(parents=True, exist_ok=True)

    unchanged = _unchanged_publication_result(
        client,
        bucket,
        previous_raw=initial_raw,
        previous_root=previous_root,
        previous_sequence=previous_sequence,
        candidates=by_major,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
        initial_inventory=initial_inventory,
        trusted_public_keys=trusted_public_keys,
        output_dir=root_dir,
    )
    if unchanged is not None:
        return unchanged

    if previous_sequence is not None and release_sequence <= previous_sequence:
        raise ValueError("release_sequence must be greater than the published reference root sequence")

    prepared = _prepare_contract_window(
        client,
        bucket,
        candidates=by_major,
        previous_root=previous_root,
        output_dir=root_dir,
    )
    _upload_prepared_contracts(client, bucket, prepared)

    # A root update can reuse one contract target while another contract changes
    # (for example C1 unchanged when C2 is introduced). Verify every mandatory
    # active full against authoritative object state before signing the new root;
    # otherwise a missing reused N-1 artifact could be re-advertised indefinitely.
    _ensure_window_full_artifacts(
        client,
        bucket,
        candidates=by_major,
        entries={major: contract.entry for major, contract in prepared.items()},
        output_dir=root_dir,
    )
    _assert_root_unchanged(
        client,
        bucket,
        initial_raw=initial_raw,
        initial_etag=initial_etag,
        trusted_public_keys=trusted_public_keys,
    )

    root = _build_root(
        prepared,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
        created_at=created_at,
    )
    body = _encode_root(root, signer, release_sequence)
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
    trusted_public_keys: dict[str, bytes] | None = None,
) -> dict:
    return _publish_loaded_contract_window(
        client,
        bucket,
        [_load_candidate(candidate) for candidate in candidates],
        output_dir,
        signer=signer,
        release_sequence=release_sequence,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
        created_at=created_at,
        allow_early_retirement=allow_early_retirement,
        trusted_public_keys=trusted_public_keys,
    )


def publish_verified_contract_window(
    client,
    bucket: str,
    artifacts: list[VerifiedContractArtifact],
    output_dir: str | Path,
    *,
    signer: ReleaseSigner | KmsReleaseSigner,
    release_sequence: int,
    current_contract_major: int,
    minimum_supported_contract_major: int,
    created_at: str | None = None,
    allow_early_retirement: bool = False,
    trusted_public_keys: dict[str, bytes] | None = None,
) -> dict:
    return _publish_loaded_contract_window(
        client,
        bucket,
        [_load_verified_artifact(artifact) for artifact in artifacts],
        output_dir,
        signer=signer,
        release_sequence=release_sequence,
        current_contract_major=current_contract_major,
        minimum_supported_contract_major=minimum_supported_contract_major,
        created_at=created_at,
        allow_early_retirement=allow_early_retirement,
        trusted_public_keys=trusted_public_keys,
    )


def publish_contract_window_from_env(
    target_db: str | Path,
    mobile_manifest_path: str | Path,
    output_dir: str | Path,
    *,
    created_at: str | None = None,
    progress=None,
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
    signer = release_signer_from_env()
    return publish_contract_window(
        client_from_env(),
        bucket,
        [
            ContractReleaseCandidate(
                major,
                target_db,
                manifest_path,
                verifier=_strict_verifier_with_progress(implementation_for(major), progress),
            )
        ],
        output_dir,
        signer=signer,
        release_sequence=release_sequence_from_env(),
        trusted_public_keys=trusted_public_keys_from_env(signer=signer),
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
    progress=None,
) -> dict:
    bucket, majors, client, signer, effective_retirement, selected_majors, minimum_supported = (
        _publication_context_from_env(retire_previous_contract)
    )
    root = Path(contract_dir)
    candidates = []
    for major in selected_majors:
        candidates.append(
            ContractReleaseCandidate(
                major,
                root / f"contract-{major}.sqlite",
                root / f"contract-{major}.manifest.json",
                verifier=_strict_verifier_with_progress(implementation_for(major), progress),
            )
        )
    return publish_contract_window(
        client,
        bucket,
        candidates,
        output_dir,
        signer=signer,
        release_sequence=release_sequence_from_env(),
        trusted_public_keys=trusted_public_keys_from_env(signer=signer),
        current_contract_major=majors[-1],
        minimum_supported_contract_major=minimum_supported,
        created_at=created_at,
        allow_early_retirement=effective_retirement,
    )


def _publication_context_from_env(retire_previous_contract: bool):
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("R2_BUCKET is required")
    majors = supported_contract_majors()
    client = client_from_env()
    signer = release_signer_from_env()
    trusted_public_keys = trusted_public_keys_from_env(signer=signer)
    retirement_active = False
    if not retire_previous_contract and len(majors) == 2:
        _, _, published_root, _ = _read_root(
            client,
            bucket,
            trusted_public_keys=trusted_public_keys,
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
    return (
        bucket,
        majors,
        client,
        signer,
        effective_retirement,
        selected_majors,
        minimum_supported,
    )


def build_and_publish_contract_window_from_env(
    canonical_db: str | Path,
    contract_dir: str | Path,
    output_dir: str | Path,
    *,
    created_at: str | None = None,
    retire_previous_contract: bool = False,
    allow_previous_failure: bool = False,
    progress=None,
) -> dict:
    """Build and publish one verified window without serializing trust between phases."""
    bucket, majors, client, signer, effective_retirement, selected_majors, minimum_supported = (
        _publication_context_from_env(retire_previous_contract)
    )
    # Resolve and validate the sequence before starting the expensive build.
    # The publisher validates it again at the external-state boundary.
    release_sequence = release_sequence_from_env()
    _validate_release_sequence(release_sequence)
    build, artifacts = build_supported_contract_artifacts(
        canonical_db,
        contract_dir,
        allow_previous_failure=allow_previous_failure,
        progress=progress,
    )
    selected = [artifact for artifact in artifacts if artifact.contract_major in selected_majors]
    published = publish_verified_contract_window(
        client,
        bucket,
        selected,
        output_dir,
        signer=signer,
        release_sequence=release_sequence,
        trusted_public_keys=trusted_public_keys_from_env(signer=signer),
        current_contract_major=majors[-1],
        minimum_supported_contract_major=minimum_supported,
        created_at=created_at,
        allow_early_retirement=effective_retirement,
    )
    return {**published, "build": build}


__all__ = [
    "ContractReleaseCandidate",
    "FULL_SNAPSHOT_RETENTION",
    "MAX_ACTIVE_CONTRACTS",
    "MAX_PATCH_BASES",
    "PROTOCOL_VERSION",
    "RELEASE_PREFIX",
    "ROOT_KEY",
    "build_and_publish_contract_window_from_env",
    "publish_contract_window",
    "publish_contract_directory_from_env",
    "publish_verified_contract_window",
    "publish_contract_window_from_env",
]
