from __future__ import annotations

import json
import os
from pathlib import Path

from .job_lifecycle import fingerprint_inputs


MOBILE_BUILD_JOB_VERSION = 1


def mobile_build_input_fingerprint(
    canonical_db: str | Path,
    *,
    contract_major: int,
    physical_policy_version: str,
    product_rule_criteria_view_ddl: str,
) -> str:
    return fingerprint_inputs(
        {"canonical_db": Path(canonical_db)},
        context={
            "job_version": MOBILE_BUILD_JOB_VERSION,
            "contract_major": contract_major,
            "physical_policy_version": physical_policy_version,
            "product_rule_criteria_view_ddl": product_rule_criteria_view_ddl,
        },
    )


def write_manifest_atomic(path: str | Path, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".write")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


__all__ = [
    "MOBILE_BUILD_JOB_VERSION",
    "mobile_build_input_fingerprint",
    "write_manifest_atomic",
]