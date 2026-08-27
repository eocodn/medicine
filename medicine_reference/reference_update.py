from __future__ import annotations

from pathlib import Path

from .reference_contracts.v1 import (
    REFERENCE_CONTRACT_MAJOR,
    normalized_contract_major,
    verify_reference_database as verify_reference_database_v1,
)


def verify_reference_database(
    database: str | Path,
    expected_contract_major: int | str,
    expected_dataset_id: str,
) -> dict:
    """Dispatch runtime verification to the APK's versioned contract implementation."""
    major = normalized_contract_major(expected_contract_major)
    if major != REFERENCE_CONTRACT_MAJOR:
        raise ValueError("reference contract major is unsupported by this runtime")
    return verify_reference_database_v1(
        database,
        expected_contract_major=major,
        expected_dataset_id=expected_dataset_id,
    )


__all__ = ["REFERENCE_CONTRACT_MAJOR", "verify_reference_database"]