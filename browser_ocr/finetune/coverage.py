from __future__ import annotations

from collections.abc import Mapping

from .dataset import DatasetError


_REQUIREMENTS = (
    ("required_document_types", "document_types", "missing_document_types"),
    ("required_scripts", "scripts", "missing_scripts"),
    ("required_semantic_strata", "semantic_tags", "missing_semantic_strata"),
    ("required_risk_strata", "risk_tags", "missing_risk_strata"),
)


def _required_list(plan: Mapping[str, object], key: str) -> list[str]:
    value = plan.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise DatasetError(f"research plan {key} must be a non-empty string array")
    if len(value) != len(set(value)):
        raise DatasetError(f"research plan {key} must not contain duplicates")
    return value


def audit_coverage(
    stats: Mapping[str, object],
    plan: Mapping[str, object],
    *,
    minimum_per_stratum: int = 1,
) -> dict:
    if not isinstance(minimum_per_stratum, int) or minimum_per_stratum <= 0:
        raise DatasetError("minimum_per_stratum must be a positive integer")
    report: dict[str, object] = {
        "schema_version": 1,
        "minimum_per_stratum": minimum_per_stratum,
    }
    missing_total = 0
    observed: dict[str, dict[str, int]] = {}
    for plan_key, stats_key, report_key in _REQUIREMENTS:
        required = _required_list(plan, plan_key)
        counts = stats.get(stats_key)
        if not isinstance(counts, Mapping):
            raise DatasetError(f"dataset stats {stats_key} must be an object")
        normalized_counts: dict[str, int] = {}
        for item in required:
            count = counts.get(item, 0)
            if not isinstance(count, int) or count < 0:
                raise DatasetError(f"dataset stats {stats_key}.{item} must be a non-negative integer")
            normalized_counts[item] = count
        missing = sorted(item for item in required if normalized_counts[item] < minimum_per_stratum)
        report[report_key] = missing
        observed[plan_key] = normalized_counts
        missing_total += len(missing)
    report["observed"] = observed
    report["status"] = "ok" if missing_total == 0 else "insufficient"
    return report
