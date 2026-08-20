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
    verify_built: Verifier | None = None


@dataclass(frozen=True)
class VerifiedContractArtifact:
    contract_major: int
    database: Path
    manifest: Path
    dataset_id: str
    sha256: str
    size_bytes: int


_IMPLEMENTATIONS: dict[int, ReferenceContractImplementation] = {
    v1.REFERENCE_CONTRACT_MAJOR: ReferenceContractImplementation(
        contract_major=v1.REFERENCE_CONTRACT_MAJOR,
        export=v1.export_reference_database,
        verify=v1.verify_reference_database,
        verify_built=v1.verify_built_reference_database,
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


def build_supported_contract_artifacts(
    canonical_db: str | Path,
    output_dir: str | Path,
    *,
    allow_previous_failure: bool = False,
    progress=None,
) -> tuple[dict, list[VerifiedContractArtifact]]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    contracts: list[dict] = []
    artifacts: list[VerifiedContractArtifact] = []
    majors = supported_contract_majors()
    current = majors[-1]
    failed_previous: dict | None = None
    for major in majors:
        implementation = implementation_for(major)
        database = root / f"contract-{major}.sqlite"
        manifest = root / f"contract-{major}.manifest.json"
        database.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        try:
            result = implementation.export(
                canonical_db,
                database,
                manifest_path=manifest,
                progress=progress,
            )
            verifier = implementation.verify_built or implementation.verify
            verifier(database, major, str(result["dataset_id"]))
        except Exception as exc:
            database.unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            if allow_previous_failure and major == current - 1:
                failed_previous = {
                    "contract_major": major,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                }
                continue
            raise
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
        artifacts.append(
            VerifiedContractArtifact(
                contract_major=major,
                database=database,
                manifest=manifest,
                dataset_id=str(result["dataset_id"]),
                sha256=str(result["sha256"]),
                size_bytes=int(result["size_bytes"]),
            )
        )
    payload = {
        "current_contract_major": majors[-1],
        "minimum_supported_contract_major": majors[0],
        "contracts": contracts,
    }
    if failed_previous is not None:
        payload["failed_previous_contract"] = failed_previous
    return payload, artifacts


def build_supported_contract_window(
    canonical_db: str | Path,
    output_dir: str | Path,
    *,
    allow_previous_failure: bool = False,
    progress=None,
) -> dict:
    payload, _ = build_supported_contract_artifacts(
        canonical_db,
        output_dir,
        allow_previous_failure=allow_previous_failure,
        progress=progress,
    )
    return payload


__all__ = [
    "ReferenceContractImplementation",
    "VerifiedContractArtifact",
    "build_supported_contract_artifacts",
    "build_supported_contract_window",
    "implementation_for",
    "supported_contract_majors",
]
