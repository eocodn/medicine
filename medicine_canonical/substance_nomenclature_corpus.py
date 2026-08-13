from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .substance_external import ExternalEvidence
from .substance_sources import FDA_GSRS_UNII_NAMES_DATASET_KEY


APPROVED_NOMENCLATURE_CORPUS_PATH = (
    Path(__file__).with_name("data") / "substance_nomenclature_aliases.tsv"
)


@dataclass(frozen=True)
class ApprovedNomenclatureAlias:
    observed_name: str
    target_unii: str
    external_evidence_name: str
    review_basis: str
    reviewed_at: str


def corpus_sha256(path: str | Path = APPROVED_NOMENCLATURE_CORPUS_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_approved_nomenclature_corpus(
    path: str | Path,
    normalize_name: Callable[[object], str],
) -> dict[str, ApprovedNomenclatureAlias]:
    corpus_path = Path(path)
    with corpus_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected = [
            "observed_name",
            "target_unii",
            "external_evidence_name",
            "review_basis",
            "reviewed_at",
        ]
        if reader.fieldnames != expected:
            raise ValueError(
                f"invalid nomenclature corpus header: expected {expected}, got {reader.fieldnames}"
            )
        result: dict[str, ApprovedNomenclatureAlias] = {}
        for row_number, row in enumerate(reader, start=2):
            values = {key: str(row.get(key) or "").strip() for key in expected}
            missing = [key for key, value in values.items() if not value]
            if missing:
                raise ValueError(f"invalid nomenclature corpus row {row_number}: missing {missing}")
            observed_key = normalize_name(values["observed_name"])
            if not observed_key:
                raise ValueError(f"invalid nomenclature corpus row {row_number}: empty observed_name")
            if observed_key in result:
                raise ValueError(
                    f"duplicate observed_name in nomenclature corpus: {values['observed_name']}"
                )
            result[observed_key] = ApprovedNomenclatureAlias(**values)
    return result


def validate_approved_nomenclature_corpus(
    corpus: dict[str, ApprovedNomenclatureAlias],
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    *,
    active_observed_names: set[str] | None = None,
) -> dict[str, ApprovedNomenclatureAlias]:
    validated: dict[str, ApprovedNomenclatureAlias] = {}
    for observed_key, row in sorted(corpus.items()):
        if active_observed_names is not None and observed_key not in active_observed_names:
            continue
        evidence_key = normalize_name(row.external_evidence_name)
        candidates = external.get(evidence_key, {})
        if len(candidates) != 1:
            raise ValueError(
                "approved nomenclature evidence must resolve uniquely in trusted external names: "
                f"{row.observed_name!r} -> {row.external_evidence_name!r} ({sorted(candidates)})"
            )
        selected_unii, evidence = next(iter(candidates.items()))
        if selected_unii != row.target_unii:
            raise ValueError(
                f"approved nomenclature pinned UNII mismatch for {row.observed_name!r}: "
                f"expected {row.target_unii}, evidence resolves to {selected_unii}"
            )
        if evidence.dataset_key != FDA_GSRS_UNII_NAMES_DATASET_KEY:
            raise ValueError(
                f"approved nomenclature evidence must come from FDA GSRS Names: {row.observed_name!r}"
            )
        if row.external_evidence_name not in evidence.names:
            raise ValueError(
                f"approved nomenclature exact evidence name missing for {row.observed_name!r}"
            )
        validated[observed_key] = row
    return validated


__all__ = [
    "APPROVED_NOMENCLATURE_CORPUS_PATH",
    "ApprovedNomenclatureAlias",
    "corpus_sha256",
    "load_approved_nomenclature_corpus",
    "validate_approved_nomenclature_corpus",
]