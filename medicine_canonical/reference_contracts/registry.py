from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import v1


Exporter = Callable[..., dict]
Verifier = Callable[[str | Path, int, str], dict]


@dataclass(frozen=True)
class ReferenceContractImplementation:
    contract_major: int
    export: Exporter
    verify: Verifier


_IMPLEMENTATIONS: dict[int, ReferenceContractImplementation] = {
    v1.REFERENCE_CONTRACT_MAJOR: ReferenceContractImplementation(
        contract_major=v1.REFERENCE_CONTRACT_MAJOR,
        export=v1.export_reference_database,
        verify=v1.verify_reference_database,
    ),
}


def implementation_for(contract_major: int) -> ReferenceContractImplementation:
    try:
        return _IMPLEMENTATIONS[contract_major]
    except KeyError as exc:
        raise ValueError(f"unsupported reference contract major: {contract_major}") from exc


def supported_contract_majors() -> tuple[int, ...]:
    majors = tuple(sorted(_IMPLEMENTATIONS))
    if not majors:
        raise RuntimeError("no reference contract implementations are registered")
    current = majors[-1]
    expected = (current,) if current == 1 else (current - 1, current)
    if majors != expected:
        raise RuntimeError(
            "registered reference contracts must contain exactly current N and previous N-1"
        )
    return majors


def build_supported_contract_window(
    canonical_db: str | Path,
    output_dir: str | Path,
) -> dict:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    contracts: list[dict] = []
    for major in supported_contract_majors():
        implementation = implementation_for(major)
        database = root / f"contract-{major}.sqlite"
        manifest = root / f"contract-{major}.manifest.json"
        result = implementation.export(
            canonical_db,
            database,
            manifest_path=manifest,
        )
        implementation.verify(database, major, str(result["dataset_id"]))
        contracts.append(
            {
                "contract_major": major,
                "database": str(database),
                "manifest": str(manifest),
                "dataset_id": result["dataset_id"],
                "sha256": result["sha256"],
                "size_bytes": result["size_bytes"],
            }
        )
    majors = supported_contract_majors()
    return {
        "current_contract_major": majors[-1],
        "minimum_supported_contract_major": majors[0],
        "contracts": contracts,
    }


__all__ = [
    "ReferenceContractImplementation",
    "build_supported_contract_window",
    "implementation_for",
    "supported_contract_majors",
]