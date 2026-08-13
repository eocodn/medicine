from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .substance_external import ExternalEvidence


@dataclass(frozen=True)
class MatchEvidence:
    unii: str
    external_name: str
    dataset_key: str
    match_method: str


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
    first = resolved[0]
    return [first]


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


def candidates_for_local_name(
    local_name: str,
    external: dict[str, dict[str, ExternalEvidence]],
    normalize_name: Callable[[object], str],
) -> list[MatchEvidence]:
    exact = _all_exact(local_name, external, normalize_name)
    if exact:
        return exact
    for resolver in (_wrapper_candidate, _declared_alias_candidate, _typography_candidate):
        candidate = resolver(local_name, external, normalize_name)
        if candidate:
            return candidate
    return []


__all__ = ["MatchEvidence", "candidates_for_local_name"]