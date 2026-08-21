from __future__ import annotations

import re
from dataclasses import dataclass

from .product_search_components import split_ingredient_components
from .product_search_numeric import normalize_number
from medicine_reference.product_search_text import (
    UNIT_SENTINEL,
    canonical_search_text,
    character_ngrams,
    normalized_unit_text,
)


_SEARCH_MODES = {"manual", "ocr"}
_MANUAL_SIMILARITY_THRESHOLD = 0.94
_OCR_SIMILARITY_THRESHOLD = 0.62
_MAX_FTS_TRIGRAMS = 32
_QUALIFIER_TOKEN_RE = re.compile(
    r"__unit_(mg|ug|ml|g|iu|pct)__|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|"
    r"[0-9]+(?:\.[0-9]+)?|\.[0-9]+|/",
    re.IGNORECASE,
)
_NUMBER_PATTERN = r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_EXPLICIT_QUALIFIER_GROUP_RE = re.compile(
    rf"(?:{_NUMBER_PATTERN}\s*/\s*)*{_NUMBER_PATTERN}\s*__unit_(?:mg|ug|ml|g|iu|pct)__",
    re.IGNORECASE,
)
_OCR_STRENGTH_UNIT_PATTERN = (
    r"(?:mcg|ug|μg|µg|mg|ml|iu|g|%|㎍|㎎|㎖|"
    r"마이크로(?:그램|그람)|밀리(?:그램|그람)|밀리리터|그램|그람|아이유|단위)"
)
_OCR_DOSE_AMOUNT_PATTERN = r"(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?|[¼½¾⅐-⅟↉])"
_OCR_TRAILING_REGIMEN_RE = re.compile(
    rf"(?P<unit>{_OCR_STRENGTH_UNIT_PATTERN})\s*{_OCR_DOSE_AMOUNT_PATTERN}"
    rf"\s*(?:정|캡슐|포|tablets?|capsules?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProductSearchQuery:
    original: str
    mode: str
    normalized: str
    rank_text: str
    explicit_qualifiers: tuple[tuple[str, str], ...]

    @property
    def identifier_like(self) -> bool:
        return bool(re.fullmatch(r"[a-z0-9._/-]+", self.original, re.IGNORECASE))


@dataclass(frozen=True)
class ProductSearchMatch:
    field: str
    tier: str
    fuzzy: bool
    similarity: float
    sort_key: tuple[int, ...]

    def explanation(self) -> dict[str, object]:
        return {
            "field": self.field,
            "tier": self.tier,
            "fuzzy": self.fuzzy,
            "similarity": round(self.similarity, 4),
            "sort_key": list(self.sort_key),
        }


def _qualifier_free_search_text(value: object) -> str:
    """Remove only explicit NUMBER+known-unit groups before character ranking."""
    text = _EXPLICIT_QUALIFIER_GROUP_RE.sub(" ", normalized_unit_text(value))
    for unit, sentinel in UNIT_SENTINEL.items():
        text = text.replace(sentinel, unit)
    return "".join(char for char in text if char.isalnum())


def _explicit_qualifiers(value: object) -> tuple[tuple[str, str], ...]:
    normalized = normalized_unit_text(value)
    tokens = list(_QUALIFIER_TOKEN_RE.finditer(normalized))
    qualifiers: list[tuple[str, str]] = []
    numbers: list[tuple[str, int, int]] = []
    for index, token_match in enumerate(tokens):
        raw = token_match.group(0)
        unit = token_match.group(1)
        if raw == "/":
            continue
        if unit:
            canonical_unit = unit.casefold()
            if index > 0:
                previous = tokens[index - 1]
                number = normalize_number(previous.group(0))
                if number is not None:
                    bound: list[tuple[str, str]] = [(number, canonical_unit)]
                    number_index = len(numbers) - 2
                    while number_index >= 0:
                        current = numbers[number_index]
                        following = numbers[number_index + 1]
                        between = normalized[current[2]:following[1]].strip()
                        if between != "/":
                            break
                        bound.append((current[0], canonical_unit))
                        number_index -= 1
                    qualifiers.extend(reversed(bound))
            continue
        number = normalize_number(raw)
        if number is not None:
            numbers.append((number, token_match.start(), token_match.end()))
    return tuple(qualifiers)


def _strip_ocr_trailing_regimen(value: str) -> str:
    return _OCR_TRAILING_REGIMEN_RE.sub(lambda match: match.group("unit"), value)


def parse_product_search_query(value: object, *, mode: str = "manual") -> ProductSearchQuery:
    normalized_mode = str(mode or "manual").strip().lower()
    if normalized_mode not in _SEARCH_MODES:
        raise ValueError("search mode must be manual or ocr")
    original = str(value or "").strip()
    search_text = _strip_ocr_trailing_regimen(original) if normalized_mode == "ocr" else original
    explicit_qualifiers = _explicit_qualifiers(search_text)
    normalized = canonical_search_text(search_text)
    rank_text = (
        _qualifier_free_search_text(search_text)
        if explicit_qualifiers
        else normalized
    )
    return ProductSearchQuery(
        original=original,
        mode=normalized_mode,
        normalized=normalized,
        rank_text=rank_text,
        explicit_qualifiers=explicit_qualifiers,
    )


def ocr_candidate_bigrams(query: ProductSearchQuery) -> tuple[str, ...]:
    if query.mode != "ocr":
        return ()
    return character_ngrams(query.normalized, 2)[:24]


def fts_candidate_terms(query: ProductSearchQuery) -> tuple[str, ...]:
    text = query.normalized
    if len(text) < 3:
        return ()
    trigrams = tuple(dict.fromkeys(text[index:index + 3] for index in range(len(text) - 2)))
    if len(trigrams) <= _MAX_FTS_TRIGRAMS:
        return trigrams
    last = len(trigrams) - 1
    indexes = sorted({round(index * last / (_MAX_FTS_TRIGRAMS - 1)) for index in range(_MAX_FTS_TRIGRAMS)})
    return tuple(trigrams[index] for index in indexes)


def _ordered_qualifier_match(
    needle: tuple[tuple[str, str], ...],
    haystack: tuple[tuple[str, str], ...],
) -> bool:
    if not needle:
        return True
    cursor = 0
    for candidate in haystack:
        if candidate == needle[cursor]:
            cursor += 1
            if cursor == len(needle):
                return True
    return False


_OCR_CONFUSABLE_PAIRS = frozenset({
    frozenset(("0", "o")),
    frozenset(("1", "i")),
    frozenset(("1", "l")),
})


def _substring_edit_similarity(query: str, candidate: str, *, ocr: bool) -> float:
    """Score the best candidate substring with one bounded edit-distance pass.

    Candidate prefix/suffix characters are free, while insertions inside the
    aligned span, deletions, and substitutions cost edits. OCR-confusable
    substitutions receive a reduced cost. This replaces the old repeated
    SequenceMatcher/window scans with O(len(query) * len(candidate)) work.
    """
    if not query or not candidate:
        return 0.0
    previous = [0.0] * (len(candidate) + 1)
    for query_index, query_char in enumerate(query, start=1):
        current = [float(query_index)]
        for candidate_index, candidate_char in enumerate(candidate, start=1):
            if query_char == candidate_char:
                substitution = 0.0
            elif ocr and frozenset((query_char, candidate_char)) in _OCR_CONFUSABLE_PAIRS:
                substitution = 0.2
            else:
                substitution = 1.0
            current.append(min(
                previous[candidate_index] + 1.0,
                current[candidate_index - 1] + 1.0,
                previous[candidate_index - 1] + substitution,
            ))
        previous = current
    distance = min(previous)
    return max(0.0, 1.0 - distance / max(len(query), 1))


def _ordered_subsequence_similarity(query: str, candidate: str) -> float:
    """Reward insertion-only spelling variation without treating substitutions as equivalent."""
    if not query or not candidate:
        return 0.0
    cursor = 0
    first = -1
    last = -1
    for index, char in enumerate(candidate):
        if char != query[cursor]:
            continue
        if first < 0:
            first = index
        last = index
        cursor += 1
        if cursor == len(query):
            span = last - first + 1
            density = len(query) / max(span, len(query))
            return 0.94 + 0.06 * density
    return 0.0


def _best_similarity(query: str, candidate: str, *, ocr: bool) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate or query in candidate:
        return 1.0
    insertion_only = _ordered_subsequence_similarity(query, candidate)
    if not ocr:
        return insertion_only
    return max(insertion_only, _substring_edit_similarity(query, candidate, ocr=True))

def _match_one_field(
    query: ProductSearchQuery,
    value: object,
    *,
    field: str,
    field_rank: int,
) -> ProductSearchMatch | None:
    candidate = canonical_search_text(value)
    if not query.normalized or not candidate:
        return None
    if not _ordered_qualifier_match(query.explicit_qualifiers, _explicit_qualifiers(value)):
        return None

    normalized = query.rank_text
    if query.explicit_qualifiers:
        qualifier_free_candidate = _qualifier_free_search_text(value)
        if not normalized:
            return ProductSearchMatch(
                field=field,
                tier="qualifier",
                fuzzy=False,
                similarity=1.0,
                sort_key=(field_rank, 0, 0, 0, len(candidate)),
            )
        candidate = qualifier_free_candidate
    if not candidate:
        return None
    if candidate == normalized:
        tier_rank, tier, similarity, position = 0, "exact", 1.0, 0
    elif candidate.startswith(normalized):
        tier_rank, tier, similarity, position = 1, "prefix", 1.0, 0
    elif normalized in candidate:
        tier_rank, tier, similarity = 2, "substring", 1.0
        position = candidate.find(normalized)
    else:
        ocr_tolerance = query.mode == "ocr" and field == "product_name"
        similarity = _best_similarity(normalized, candidate, ocr=ocr_tolerance)
        threshold = _OCR_SIMILARITY_THRESHOLD if ocr_tolerance else _MANUAL_SIMILARITY_THRESHOLD
        if similarity < threshold:
            return None
        tier_rank, tier, position = 3, "similarity", 0

    gap = abs(len(candidate) - len(normalized))
    error = round((1.0 - similarity) * 10_000)
    return ProductSearchMatch(
        field=field,
        tier=tier,
        fuzzy=tier == "similarity",
        similarity=similarity,
        sort_key=(field_rank, tier_rank, error, position, gap),
    )


def _match_ingredient_field(query: ProductSearchQuery, value: object) -> ProductSearchMatch | None:
    matches = [
        _match_one_field(query, component, field="ingredient_text", field_rank=1)
        for component in split_ingredient_components(value)
    ]
    found = [match for match in matches if match is not None]
    return min(found, key=lambda match: match.sort_key) if found else None


def match_product_fields(
    query: ProductSearchQuery,
    *,
    product_name: object,
    ingredient_text: object = None,
    manufacturer: object = None,
) -> ProductSearchMatch | None:
    product_match = _match_one_field(query, product_name, field="product_name", field_rank=0)
    if product_match is not None:
        return product_match
    ingredient_match = _match_ingredient_field(query, ingredient_text)
    if ingredient_match is not None:
        return ingredient_match
    return _match_one_field(query, manufacturer, field="manufacturer", field_rank=2)


__all__ = [
    "ProductSearchMatch",
    "ProductSearchQuery",
    "canonical_search_text",
    "fts_candidate_terms",
    "match_product_fields",
    "ocr_candidate_bigrams",
    "parse_product_search_query",
]
