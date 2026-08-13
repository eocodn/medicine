from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .substance_external import ExternalEvidence


APPROVED_TYPO_CORPUS_PATH = Path(__file__).with_name("data") / "substance_typo_aliases.tsv"


@dataclass(frozen=True)
class ApprovedTypoAlias:
    observed_name: str
    target_name: str
    target_unii: str
    review_basis: str
    reviewed_at: str


def corpus_sha256(path: str | Path = APPROVED_TYPO_CORPUS_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_approved_typo_corpus(
    path: str | Path,
    normalize_name: Callable[[object], str],
) -> dict[str, ApprovedTypoAlias]:
    corpus_path = Path(path)
    with corpus_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected = ["observed_name", "target_name", "target_unii", "review_basis", "reviewed_at"]
        if reader.fieldnames != expected:
            raise ValueError(f"invalid typo corpus header: expected {expected}, got {reader.fieldnames}")
        result: dict[str, ApprovedTypoAlias] = {}
        for row_number, row in enumerate(reader, start=2):
            values = {key: str(row.get(key) or "").strip() for key in expected}
            missing = [key for key, value in values.items() if not value]
            if missing:
                raise ValueError(f"invalid typo corpus row {row_number}: missing {missing}")
            observed_key = normalize_name(values["observed_name"])
            target_key = normalize_name(values["target_name"])
            if not observed_key or observed_key == target_key:
                raise ValueError(f"invalid typo corpus row {row_number}: observed and target must differ")
            if observed_key in result:
                raise ValueError(f"duplicate observed_name in typo corpus: {values['observed_name']}")
            result[observed_key] = ApprovedTypoAlias(**values)
    return result


def validate_approved_typo_corpus(
    corpus: dict[str, ApprovedTypoAlias],
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    *,
    active_observed_names: set[str] | None = None,
) -> dict[str, ApprovedTypoAlias]:
    validated: dict[str, ApprovedTypoAlias] = {}
    for observed_key, row in sorted(corpus.items()):
        if active_observed_names is not None and observed_key not in active_observed_names:
            continue
        target_key = normalize_name(row.target_name)
        candidates = external.get(target_key, {})
        if len(candidates) != 1:
            raise ValueError(
                "approved typo target must resolve uniquely in trusted external names: "
                f"{row.observed_name!r} -> {row.target_name!r} ({sorted(candidates)})"
            )
        selected_unii = next(iter(candidates))
        if selected_unii != row.target_unii:
            raise ValueError(
                f"approved typo pinned UNII mismatch for {row.observed_name!r}: "
                f"expected {row.target_unii}, external target resolves to {selected_unii}"
            )
        if observed_key != normalize_name(row.observed_name):
            raise ValueError(f"approved typo corpus key mismatch for {row.observed_name!r}")
        validated[observed_key] = row
    return validated


__all__ = [
    "APPROVED_TYPO_CORPUS_PATH",
    "ApprovedTypoAlias",
    "corpus_sha256",
    "load_approved_typo_corpus",
    "validate_approved_typo_corpus",
]