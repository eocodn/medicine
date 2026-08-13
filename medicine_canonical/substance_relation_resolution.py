from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .substance_matching import MatchEvidence, RelationEvidence, relation_for_local_name
from .substance_reviewed_relations import (
    ApprovedFormRelation,
    validate_active_form_relation_corpus,
)


@dataclass(frozen=True)
class SelectedSourceRelation:
    relation: RelationEvidence
    match_method: str
    base_unii: str | None = None
    review_basis: str | None = None
    reviewed_at: str | None = None


def select_source_relations(
    representatives: dict[str, str],
    name_evidence: dict[str, dict[str, MatchEvidence]],
    name_to_substance: dict[str, str],
    reviewed_form_relations: dict[str, ApprovedFormRelation],
    normalize_name: Callable[[object], str],
) -> dict[str, SelectedSourceRelation]:
    base_unii_by_name = {
        name: next(iter(candidates))
        for name, candidates in name_evidence.items()
        if len(candidates) == 1
    }
    unresolved_names = {
        name for name in representatives if not name_evidence[name]
    }
    reviewed = validate_active_form_relation_corpus(
        reviewed_form_relations,
        base_unii_by_name,
        normalize_name,
        active_observed_names=unresolved_names,
    )
    selected: dict[str, SelectedSourceRelation] = {}
    for normalized_name in sorted(representatives):
        if name_evidence[normalized_name]:
            continue
        reviewed_row = reviewed.get(normalized_name)
        if reviewed_row is not None:
            base_name = normalize_name(reviewed_row.base_name)
            if base_name not in representatives:
                raise ValueError(
                    f"reviewed form relation base is not locally observed: {reviewed_row.base_name!r}"
                )
            relation = RelationEvidence(
                base_name,
                reviewed_row.relation_type,
                reviewed_row.qualifier,
            )
            if name_to_substance[normalized_name] == name_to_substance[base_name]:
                raise ValueError(
                    f"reviewed form relation cannot be self-referential: {reviewed_row.observed_name!r}"
                )
            selected[normalized_name] = SelectedSourceRelation(
                relation=relation,
                match_method="reviewed_source_form_relation",
                base_unii=reviewed_row.base_unii,
                review_basis=reviewed_row.review_basis,
                reviewed_at=reviewed_row.reviewed_at,
            )
            continue

        relation = relation_for_local_name(representatives[normalized_name], normalize_name)
        if relation is None or relation.base_normalized_name not in representatives:
            continue
        base_candidates = name_evidence[relation.base_normalized_name]
        if len(base_candidates) != 1:
            continue
        base_evidence = next(iter(base_candidates.values()))
        if base_evidence.match_method != "normalized_name_exact":
            continue
        if name_to_substance[normalized_name] == name_to_substance[relation.base_normalized_name]:
            continue
        selected[normalized_name] = SelectedSourceRelation(
            relation=relation,
            match_method="explicit_source_form_relation",
        )
    return selected


__all__ = ["SelectedSourceRelation", "select_source_relations"]