from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .substance_external import ExternalEvidence
from .substance_nomenclature_corpus import (
    APPROVED_NOMENCLATURE_CORPUS_PATH,
    ApprovedNomenclatureAlias,
    corpus_sha256 as nomenclature_corpus_sha256,
    load_approved_nomenclature_corpus,
    validate_approved_nomenclature_corpus,
)
from .substance_typo_corpus import (
    APPROVED_TYPO_CORPUS_PATH,
    ApprovedTypoAlias,
    corpus_sha256 as typo_corpus_sha256,
    load_approved_typo_corpus,
    validate_approved_typo_corpus,
)


@dataclass(frozen=True)
class ReviewedAliasCorpora:
    typos: dict[str, ApprovedTypoAlias]
    nomenclature: dict[str, ApprovedNomenclatureAlias]
    typo_sha256: str
    nomenclature_sha256: str


@dataclass(frozen=True)
class ActiveReviewedAliases:
    typos: dict[str, ApprovedTypoAlias]
    nomenclature: dict[str, ApprovedNomenclatureAlias]


def load_reviewed_alias_corpora(
    normalize_name: Callable[[object], str],
) -> ReviewedAliasCorpora:
    return ReviewedAliasCorpora(
        typos=load_approved_typo_corpus(APPROVED_TYPO_CORPUS_PATH, normalize_name),
        nomenclature=load_approved_nomenclature_corpus(
            APPROVED_NOMENCLATURE_CORPUS_PATH,
            normalize_name,
        ),
        typo_sha256=typo_corpus_sha256(APPROVED_TYPO_CORPUS_PATH),
        nomenclature_sha256=nomenclature_corpus_sha256(APPROVED_NOMENCLATURE_CORPUS_PATH),
    )


def validate_active_reviewed_aliases(
    corpora: ReviewedAliasCorpora,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    active_observed_names: set[str],
) -> ActiveReviewedAliases:
    return ActiveReviewedAliases(
        typos=validate_approved_typo_corpus(
            corpora.typos,
            external,
            normalize_name,
            active_observed_names=active_observed_names,
        ),
        nomenclature=validate_approved_nomenclature_corpus(
            corpora.nomenclature,
            external,
            normalize_name,
            active_observed_names=active_observed_names,
        ),
    )


def reviewed_alias_meta_rows(
    corpora: ReviewedAliasCorpora,
    active: ActiveReviewedAliases,
) -> list[tuple[str, str]]:
    return [
        ("approved_typo_corpus_rows", str(len(corpora.typos))),
        ("active_approved_typo_rows", str(len(active.typos))),
        ("approved_typo_corpus_sha256", corpora.typo_sha256),
        ("approved_nomenclature_corpus_rows", str(len(corpora.nomenclature))),
        ("active_approved_nomenclature_rows", str(len(active.nomenclature))),
        ("approved_nomenclature_corpus_sha256", corpora.nomenclature_sha256),
    ]


__all__ = [
    "ActiveReviewedAliases",
    "ReviewedAliasCorpora",
    "load_reviewed_alias_corpora",
    "reviewed_alias_meta_rows",
    "validate_active_reviewed_aliases",
]