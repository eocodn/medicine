from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .substance_external import ExternalEvidence
from .substance_nomenclature_corpus import ApprovedNomenclatureAlias
from .substance_typo_corpus import ApprovedTypoAlias


@dataclass(frozen=True)
class MatchEvidence:
    unii: str
    external_name: str
    dataset_key: str
    match_method: str


@dataclass(frozen=True)
class RelationEvidence:
    base_normalized_name: str
    relation_type: str
    qualifier: str


_FORM_QUALIFIER_RE = re.compile(
    r"\s*\((경구|경구제|주사제|정제|질내삽입링|비경구형|외용제는 제외)\)\s*$",
    re.IGNORECASE,
)
_CONTAINS_PRODUCT_RE = re.compile(r"\s*함유제제\s*$", re.IGNORECASE)
_CLASSIFICATION_RE = re.compile(r"\s*\(분류번호\s*:\s*[^)]*\)\s*$", re.IGNORECASE)
_ALIAS_PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
_ALIAS_EXCLUDED_RE = re.compile(
    r"[가-힣]|\d|strain|rdna|분류|외용|경구|주사|정제|질내|비경구|micronized",
    re.IGNORECASE,
)
_ISOTOPE_RE = re.compile(r"\(\s*(\d{1,3}\s*(?:f|i|tc|mo|lu))\s*\)", re.IGNORECASE)
_GREEK_WORDS = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta"}

MATCH_METHOD_PRIORITY = {
    "normalized_name_exact": 0,
    "approved_typo_alias": 1,
    "approved_nomenclature_alias": 2,
    "source_wrapper_exact": 3,
    "source_declared_alias": 4,
    "typography_greek": 5,
    "typography_apostrophe": 6,
    "typography_isotope": 7,
}

_FORM_RELATION_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"^dilute\s+(.+?)\s+\(\s*\1\s+[^)]*\)$", re.IGNORECASE),
        "formulation_of",
        "dilute_carrier_material",
    ),
    (
        re.compile(r"^dilute\s+(.+?)\s+solution$", re.IGNORECASE),
        "formulation_of",
        "dilute_solution",
    ),
    (re.compile(r"^dilute\s+(.+)$", re.IGNORECASE), "formulation_of", "dilute"),
    (
        re.compile(r"^(.+?)\s+enteric[- ]coated\s+granules$", re.IGNORECASE),
        "formulation_of",
        "enteric_coated_granules",
    ),
    (
        re.compile(r"^(.+?)\s+enteric\s+pellets$", re.IGNORECASE),
        "formulation_of",
        "enteric_pellets",
    ),
    (
        re.compile(r"^(.+?)\s+enteric[- ]coated\s+pellets$", re.IGNORECASE),
        "formulation_of",
        "enteric_coated_pellets",
    ),
    (
        re.compile(r"^(.+?)\s+concentrate\s+granules$", re.IGNORECASE),
        "formulation_of",
        "concentrate_granules",
    ),
    (
        re.compile(r"^(.+?)\s+sustained[- ]release\s+granules$", re.IGNORECASE),
        "formulation_of",
        "sustained_release_granules",
    ),
    (
        re.compile(r"^(.+?)\s+sustained[- ]release\s+pellets$", re.IGNORECASE),
        "formulation_of",
        "sustained_release_pellets",
    ),
    (
        re.compile(r"^(.+?)\s+s\.r\.\s+small\s+granules$", re.IGNORECASE),
        "formulation_of",
        "sustained_release_small_granules",
    ),
    (
        re.compile(r"^(.+?)\s+sphere\s+granules\s+micronized$", re.IGNORECASE),
        "formulation_of",
        "sphere_granules_micronized",
    ),
    (
        re.compile(r"^(.+?)\s+granules\s+micronized$", re.IGNORECASE),
        "formulation_of",
        "granules_micronized",
    ),
    (
        re.compile(r"^(.+?)\s+mixed\s+powder\s+micronized$", re.IGNORECASE),
        "formulation_of",
        "mixed_powder_micronized",
    ),
    (
        re.compile(
            r"^(.+?)\s+granules\s+coated\s+hydroxypropyl\s+methylcellulose$",
            re.IGNORECASE,
        ),
        "formulation_of",
        "hpmc_coated_granules",
    ),
    (
        re.compile(r"^(.+?)\s*\(\s*micronized\s*\)$", re.IGNORECASE),
        "physical_form_of",
        "micronized",
    ),
    (re.compile(r"^(.*?)\s+\(?micronized\)?$", re.IGNORECASE), "physical_form_of", "micronized"),
    (re.compile(r"^(.+?)\s+pellets$", re.IGNORECASE), "formulation_of", "pellets"),
    (
        re.compile(r"^(.+?)\s+concentrate\s+solution(?:\s+\d+(?:\.\d+)?%)?$", re.IGNORECASE),
        "formulation_of",
        "concentrate_solution",
    ),
    (
        re.compile(r"^(.+?)\s+solution(?:\s+\d+(?:\.\d+)?%)?$", re.IGNORECASE),
        "formulation_of",
        "solution",
    ),
    (re.compile(r"^(.+?)\s+concentrate$", re.IGNORECASE), "formulation_of", "concentrate"),
    (re.compile(r"^(.+?)\s+coated$", re.IGNORECASE), "formulation_of", "coated"),
    (re.compile(r"^(.*?)\s+solid dispersions?$", re.IGNORECASE), "formulation_of", "solid_dispersion"),
    (
        re.compile(r"^(.*?)\s+coated granules(?:\s*\(?\d+(?:\.\d+)?%\)?)?$", re.IGNORECASE),
        "formulation_of",
        "coated_granules",
    ),
    (
        re.compile(
            r"^(.*?)\s+(?:(?:extended[- ]release|enteric[- ]coated|enteric|sphere)\s+)?granules(?:\s*\(?\d+(?:\.\d+)?%\)?)?$",
            re.IGNORECASE,
        ),
        "formulation_of",
        "granules",
    ),
    (re.compile(r"^(.*?)\s+spray dry powder$", re.IGNORECASE), "formulation_of", "spray_dry_powder"),
    (re.compile(r"^spray dried\s+(.*?)(?:\s+\d+(?:\.\d+)?%)?$", re.IGNORECASE), "formulation_of", "spray_dried"),
    (re.compile(r"^microcrystalline\s+(.+)$", re.IGNORECASE), "physical_form_of", "microcrystalline"),
    (re.compile(r"^microencapsulated\s+(.+)$", re.IGNORECASE), "formulation_of", "microencapsulated"),
)


def _single_exact(
    name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    match_method: str,
) -> list[MatchEvidence]:
    normalized = normalize_name(name)
    candidates = external.get(normalized, {})
    if len(candidates) != 1:
        return []
    unii, evidence = next(iter(candidates.items()))
    return [
        MatchEvidence(
            unii=unii,
            external_name=sorted(evidence.names, key=lambda value: (value.casefold(), value))[0],
            dataset_key=evidence.dataset_key,
            match_method=match_method,
        )
    ]


def _all_exact(
    name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
) -> list[MatchEvidence]:
    normalized = normalize_name(name)
    result: list[MatchEvidence] = []
    for unii, evidence in sorted(external.get(normalized, {}).items()):
        result.append(
            MatchEvidence(
                unii=unii,
                external_name=sorted(evidence.names, key=lambda value: (value.casefold(), value))[0],
                dataset_key=evidence.dataset_key,
                match_method="normalized_name_exact",
            )
        )
    return result


def _wrapper_candidate(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
) -> list[MatchEvidence]:
    for pattern in (_FORM_QUALIFIER_RE, _CONTAINS_PRODUCT_RE, _CLASSIFICATION_RE):
        base = pattern.sub("", local_name).strip()
        if base != local_name.strip():
            return _single_exact(base, external, normalize_name, "source_wrapper_exact")
    return []


def _declared_alias_candidate(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
) -> list[MatchEvidence]:
    parts: list[str] | None = None
    if "=" in local_name:
        split = [part.strip() for part in local_name.split("=") if part.strip()]
        if len(split) >= 2:
            parts = split
    else:
        match = _ALIAS_PAREN_RE.match(local_name.strip())
        if match and not _ALIAS_EXCLUDED_RE.search(match.group(2)):
            parts = [match.group(1).strip(), match.group(2).strip()]
    if not parts:
        return []

    resolved: list[MatchEvidence] = []
    for part in parts:
        candidate = _single_exact(part, external, normalize_name, "source_declared_alias")
        if len(candidate) != 1:
            return []
        resolved.extend(candidate)
    if len({candidate.unii for candidate in resolved}) != 1:
        return []
    return [resolved[0]]


def _typography_candidate(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
) -> list[MatchEvidence]:
    if any(symbol in local_name for symbol in _GREEK_WORDS):
        expanded = local_name
        for symbol, word in _GREEK_WORDS.items():
            expanded = expanded.replace(symbol, word)
        candidate = _single_exact(expanded, external, normalize_name, "typography_greek")
        if candidate:
            return candidate

    if "’" in local_name or "‘" in local_name:
        straight = local_name.replace("’", "'").replace("‘", "'")
        for variant in (straight, re.sub(r"\bSt\.\s+", "St ", straight, flags=re.IGNORECASE)):
            candidate = _single_exact(variant, external, normalize_name, "typography_apostrophe")
            if candidate:
                return candidate

    if _ISOTOPE_RE.search(local_name):
        compact_token = lambda match: re.sub(r"\s+", "", match.group(1))
        variants = (
            _ISOTOPE_RE.sub(lambda match: " (" + compact_token(match) + ")", local_name),
            _ISOTOPE_RE.sub(lambda match: " " + compact_token(match), local_name),
        )
        for variant in variants:
            candidate = _single_exact(variant, external, normalize_name, "typography_isotope")
            if candidate:
                return candidate
    return []


def _approved_typo_candidate(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    approved_typos: dict[str, ApprovedTypoAlias],
) -> list[MatchEvidence]:
    row = approved_typos.get(normalize_name(local_name))
    if row is None:
        return []
    target = external.get(normalize_name(row.target_name), {})
    evidence = target.get(row.target_unii)
    if evidence is None or len(target) != 1:
        return []
    return [
        MatchEvidence(
            unii=row.target_unii,
            external_name=sorted(evidence.names, key=lambda value: (value.casefold(), value))[0],
            dataset_key=evidence.dataset_key,
            match_method="approved_typo_alias",
        )
    ]


def _approved_nomenclature_candidate(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    approved_aliases: dict[str, ApprovedNomenclatureAlias],
) -> list[MatchEvidence]:
    row = approved_aliases.get(normalize_name(local_name))
    if row is None:
        return []
    candidates = external.get(normalize_name(row.external_evidence_name), {})
    evidence = candidates.get(row.target_unii)
    if evidence is None or len(candidates) != 1:
        return []
    return [
        MatchEvidence(
            unii=row.target_unii,
            external_name=row.external_evidence_name,
            dataset_key=evidence.dataset_key,
            match_method="approved_nomenclature_alias",
        )
    ]


def candidates_for_local_name(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
    *,
    approved_typos: dict[str, ApprovedTypoAlias] | None = None,
    approved_nomenclature_aliases: dict[str, ApprovedNomenclatureAlias] | None = None,
) -> list[MatchEvidence]:
    exact = _all_exact(local_name, external, normalize_name)
    if exact:
        return exact
    if approved_typos:
        candidate = _approved_typo_candidate(local_name, external, normalize_name, approved_typos)
        if candidate:
            return candidate
    if approved_nomenclature_aliases:
        candidate = _approved_nomenclature_candidate(
            local_name,
            external,
            normalize_name,
            approved_nomenclature_aliases,
        )
        if candidate:
            return candidate
    for resolver in (_wrapper_candidate, _declared_alias_candidate, _typography_candidate):
        candidate = resolver(local_name, external, normalize_name)
        if candidate:
            return candidate
    return []


def relation_for_local_name(
    local_name: str,
    normalize_name: Callable[[object], str],
) -> RelationEvidence | None:
    for pattern, relation_type, qualifier in _FORM_RELATION_RULES:
        match = pattern.match(local_name.strip())
        if not match:
            continue
        base = normalize_name(match.group(1))
        if base and base != normalize_name(local_name):
            return RelationEvidence(base, relation_type, qualifier)
    return None


__all__ = [
    "MATCH_METHOD_PRIORITY",
    "MatchEvidence",
    "RelationEvidence",
    "candidates_for_local_name",
    "relation_for_local_name",
]
