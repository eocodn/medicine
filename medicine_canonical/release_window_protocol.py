from __future__ import annotations

import re

from .release_window_artifacts import (
    CandidateMetadata,
    FULL_SNAPSHOT_RETENTION,
    full_prefix,
)


MAX_ACTIVE_CONTRACTS = 2
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_window(
    metadata: list[CandidateMetadata],
    *,
    current_contract_major: int,
    minimum_supported_contract_major: int,
    allow_early_retirement: bool = False,
) -> dict[int, CandidateMetadata]:
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

    normal_minimum = current_contract_major if current_contract_major == 1 else current_contract_major - 1
    if minimum_supported_contract_major == current_contract_major and current_contract_major > 1:
        if not allow_early_retirement:
            raise ValueError(
                "dropping N-1 requires explicit retirement mode; normal support window must "
                "contain current N and previous N-1 contract majors"
            )
    elif minimum_supported_contract_major != normal_minimum:
        raise ValueError("supported window must contain current N and previous N-1 contract majors")

    expected = set(range(minimum_supported_contract_major, current_contract_major + 1))
    if len(expected) > MAX_ACTIVE_CONTRACTS:
        raise ValueError("supported window is limited to current N and previous N-1 contracts")
    by_major: dict[int, CandidateMetadata] = {}
    for item in metadata:
        if item.candidate.contract_major in by_major:
            raise ValueError(f"duplicate release candidate for contract {item.candidate.contract_major}")
        by_major[item.candidate.contract_major] = item
    if set(by_major) != expected:
        raise ValueError("release candidates must exactly match the signed support window")
    return by_major


def validate_root_shape(root: dict) -> None:
    current = root.get("current_contract_major")
    minimum = root.get("minimum_supported_contract_major")
    contracts = root.get("contracts")
    if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
        raise ValueError("remote reference root current contract major is invalid")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
        raise ValueError("remote reference root minimum contract major is invalid")
    if minimum > current or current - minimum > 1:
        raise ValueError("remote reference root support window is invalid")
    if current == 1 and minimum != 1:
        raise ValueError("remote reference root support window is invalid")
    if not isinstance(contracts, dict):
        raise ValueError("remote reference root contracts are invalid")
    expected = {str(major) for major in range(minimum, current + 1)}
    if set(contracts) != expected:
        raise ValueError("remote reference root contracts do not match support window")
    for major_text, entry in contracts.items():
        if not isinstance(entry, dict):
            raise ValueError(f"remote contract {major_text} entry is invalid")
        validate_contract_entry(int(major_text), entry)


def validate_contract_entry(contract_major: int, entry: dict) -> None:
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
    expected_full_prefix = full_prefix(contract_major)
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


__all__ = [
    "MAX_ACTIVE_CONTRACTS",
    "validate_contract_entry",
    "validate_root_shape",
    "validate_window",
]