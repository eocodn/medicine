from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


APPROVED_FORM_RELATION_CORPUS_PATH = (
    Path(__file__).with_name("data") / "substance_form_relations.tsv"
)
_ALLOWED_RELATION_TYPES = frozenset({"formulation_of", "physical_form_of"})


@dataclass(frozen=True)
class ApprovedFormRelation:
    observed_name: str
    base_name: str
    base_unii: str
    relation_type: str
    qualifier: str
    review_basis: str
    reviewed_at: str


def corpus_sha256(path: str | Path = APPROVED_FORM_RELATION_CORPUS_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_approved_form_relation_corpus(
    path: str | Path,
    normalize_name: Callable[[object], str],
) -> dict[str, ApprovedFormRelation]:
    corpus_path = Path(path)
    expected = [
        "observed_name",
        "base_name",
        "base_unii",
        "relation_type",
        "qualifier",
        "review_basis",
        "reviewed_at",
    ]
    with corpus_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != expected:
            raise ValueError(
                f"invalid form relation corpus header: expected {expected}, got {reader.fieldnames}"
            )
        result: dict[str, ApprovedFormRelation] = {}
        for row_number, row in enumerate(reader, start=2):
            values = {key: str(row.get(key) or "").strip() for key in expected}
            missing = [key for key, value in values.items() if not value]
            if missing:
                raise ValueError(f"invalid form relation corpus row {row_number}: missing {missing}")
            observed_key = normalize_name(values["observed_name"])
            base_key = normalize_name(values["base_name"])
            if not observed_key or not base_key or observed_key == base_key:
                raise ValueError(
                    f"invalid form relation corpus row {row_number}: observed and base must differ"
                )
            if values["relation_type"] not in _ALLOWED_RELATION_TYPES:
                raise ValueError(
                    f"invalid form relation type at row {row_number}: {values['relation_type']}"
                )
            if observed_key in result:
                raise ValueError(
                    f"duplicate observed_name in form relation corpus: {values['observed_name']}"
                )
            result[observed_key] = ApprovedFormRelation(**values)
    return result


def validate_active_form_relation_corpus(
    corpus: dict[str, ApprovedFormRelation],
    base_unii_by_name: dict[str, str],
    normalize_name: Callable[[object], str],
    *,
    active_observed_names: set[str],
) -> dict[str, ApprovedFormRelation]:
    validated: dict[str, ApprovedFormRelation] = {}
    for observed_key, row in sorted(corpus.items()):
        if observed_key not in active_observed_names:
            continue
        base_key = normalize_name(row.base_name)
        actual_unii = base_unii_by_name.get(base_key)
        if actual_unii is None:
            raise ValueError(
                f"approved form relation base is not uniquely resolved: {row.observed_name!r} -> {row.base_name!r}"
            )
        if actual_unii != row.base_unii:
            raise ValueError(
                f"approved form relation pinned UNII mismatch for {row.observed_name!r}: "
                f"expected {row.base_unii}, base resolves to {actual_unii}"
            )
        validated[observed_key] = row
    return validated


def reviewed_form_relation_meta_rows(
    corpus: dict[str, ApprovedFormRelation],
    active_count: int,
) -> list[tuple[str, str]]:
    return [
        ("approved_form_relation_corpus_rows", str(len(corpus))),
        ("active_approved_form_relation_rows", str(active_count)),
        ("approved_form_relation_corpus_sha256", corpus_sha256()),
    ]


__all__ = [
    "APPROVED_FORM_RELATION_CORPUS_PATH",
    "ApprovedFormRelation",
    "corpus_sha256",
    "load_approved_form_relation_corpus",
    "reviewed_form_relation_meta_rows",
    "validate_active_form_relation_corpus",
]