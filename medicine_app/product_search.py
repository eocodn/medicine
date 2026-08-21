from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .product_search_components import split_ingredient_components
from .product_search_numeric import normalize_number, strip_non_strength_numeric_compatibility


_SEARCH_MODES = {"manual", "ocr"}
_UNIT_SENTINEL = {
    "mg": "__unit_mg__",
    "ug": "__unit_ug__",
    "ml": "__unit_ml__",
}
# Scope boundary: this parser models medication/OCR search syntax, not every
# Unicode normalization equivalence. NFKC handles ordinary presentation forms;
# unknown letters remain text qualifiers instead of being silently discarded.
_TOKEN_RE = re.compile(
    r"__unit_(mg|ug|ml)__|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[^\W\d_]+",
    re.IGNORECASE,
)
_ASCII_UNIT_PATTERNS = (
    # After a numeric token, a known dosage unit is semantic even when export
    # text is glued to it (e.g. `150mgCapsule`). This cannot match ordinary
    # alphabetic words because the left boundary must be a digit.
    (re.compile(r"(?:(?<=\d)(?:mcg|ug|μg)|(?<![a-z])(?:mcg|ug|μg)(?![a-z]))", re.IGNORECASE), "ug"),
    (re.compile(r"(?:(?<=\d)mg|(?<![a-z])mg(?![a-z]))", re.IGNORECASE), "mg"),
    (re.compile(r"(?:(?<=\d)ml|(?<![a-z])ml(?![a-z]))", re.IGNORECASE), "ml"),
)
_KOREAN_UNIT_PATTERNS = (
    (re.compile(r"마이크로[ \t]*(?:그램|그람)", re.IGNORECASE), "ug"),
    (re.compile(r"밀리[ \t]*(?:그램|그람)", re.IGNORECASE), "mg"),
    (re.compile(r"밀리[ \t]*리터", re.IGNORECASE), "ml"),
)
_OCR_STRENGTH_UNIT_PATTERN = (
    r"(?:mcg|ug|μg|µg|mg|ml|㎍|㎎|㎖|"
    r"마이크로(?:그램|그람)|밀리(?:그램|그람)|밀리리터)"
)
_OCR_DOSE_AMOUNT_PATTERN = r"(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?|[¼½¾⅐-⅟↉])"
_OCR_TRAILING_REGIMEN_RE = re.compile(
    rf"(?P<unit>{_OCR_STRENGTH_UNIT_PATTERN})\s*{_OCR_DOSE_AMOUNT_PATTERN}"
    rf"\s*(?:정|캡슐|포|tablets?|capsules?)\s*$",
    re.IGNORECASE,
)
_RAW_UNIT_SYMBOLS = {
    "㎎": "밀리그램",
    "㎍": "마이크로그램",
    "㎖": "밀리리터",
}


@dataclass(frozen=True)
class ProductSearchQuery:
    original: str
    mode: str
    normalized: str
    text_tokens: tuple[str, ...]
    number_tokens: tuple[str, ...]
    unit_tokens: tuple[str, ...]
    strength_atoms: tuple[tuple[str, str | None], ...]

    @property
    def structured(self) -> bool:
        return bool(
            self.text_tokens
            and (
                self.number_tokens
                or self.unit_tokens
                or len(self.text_tokens) > 1
            )
        )

    @property
    def identifier_like(self) -> bool:
        return bool(re.fullmatch(r"[a-z0-9._/-]+", self.original, re.IGNORECASE))


@dataclass(frozen=True)
class ProductSearchMatch:
    field: str
    tier: str
    fuzzy: bool
    sort_key: tuple[int, ...]

    def explanation(self) -> dict[str, object]:
        return {
            "field": self.field,
            "tier": self.tier,
            "fuzzy": self.fuzzy,
            "sort_key": list(self.sort_key),
        }


def _canonical_text(value: object) -> str:
    raw = strip_non_strength_numeric_compatibility(str(value or ""))
    for symbol, unit_name in _RAW_UNIT_SYMBOLS.items():
        raw = raw.replace(symbol, f" {unit_name} ")
    text = unicodedata.normalize("NFKC", raw).casefold()
    text = text.replace("µ", "μ")
    for pattern, unit in _ASCII_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    for pattern, unit in _KOREAN_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    return text


def _strip_ocr_trailing_regimen(value: str) -> str:
    return _OCR_TRAILING_REGIMEN_RE.sub(lambda match: match.group("unit"), value)


def _scan_normalized_tokens(
    normalized: str,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[tuple[str, str | None], ...],
    tuple[str, ...],
]:
    """Tokenize one normalized field and apply slash-group unit inheritance."""
    text_tokens: list[str] = []
    number_tokens: list[str] = []
    unit_tokens: list[str] = []
    semantic_unit_tokens: list[str] = []
    strength_atoms: list[tuple[str, str | None]] = []
    number_spans: list[tuple[int, int]] = []
    matches = list(_TOKEN_RE.finditer(normalized))
    previous_kind: str | None = None
    previous_match: re.Match[str] | None = None

    for index, match in enumerate(matches):
        unit = match.group(1)
        token = match.group(0).casefold()
        if unit:
            canonical_unit = unit.casefold()
            unit_tokens.append(canonical_unit)
            inherited = 0
            if previous_kind == "number":
                number, _bound_unit = strength_atoms[-1]
                strength_atoms[-1] = (number, canonical_unit)
                atom_index = len(strength_atoms) - 2
                while atom_index >= 0 and strength_atoms[atom_index][1] is None:
                    between = normalized[
                        number_spans[atom_index][1]:number_spans[atom_index + 1][0]
                    ].strip()
                    if between != "/":
                        break
                    prior_number, _prior_unit = strength_atoms[atom_index]
                    strength_atoms[atom_index] = (prior_number, canonical_unit)
                    inherited += 1
                    atom_index -= 1
            semantic_unit_tokens.extend([canonical_unit] * (inherited + 1))
            previous_kind = "unit"
        elif token[0].isdigit() or (token.startswith(".") and token[1:].isdigit()):
            next_match = matches[index + 1] if index + 1 < len(matches) else None
            unit_follows = bool(
                next_match
                and next_match.group(1)
                and not normalized[match.end():next_match.start()].strip()
            )
            # Short code-like prefixes (`B12`, `D3`, `CRL9096`) stay lexical
            # unless an explicit dosage unit follows. Longer medication names
            # such as `Tylenol500` keep the useful compact brand+strength
            # behavior. This is intentionally a small semantic rule rather than
            # a generic alphanumeric grammar.
            adjacent_latin_name = bool(
                previous_kind == "text"
                and previous_match is not None
                and previous_match.end() == match.start()
                and text_tokens
                and text_tokens[-1].isascii()
                and text_tokens[-1].isalpha()
                and len(text_tokens[-1]) <= 3
                and not unit_follows
            )
            if adjacent_latin_name:
                text_tokens[-1] += token
                previous_kind = "text"
                previous_match = match
                continue

            number = normalize_number(token)
            if number is None:
                previous_kind = None
                previous_match = match
                continue
            number_tokens.append(number)
            strength_atoms.append((number, None))
            number_spans.append(match.span())
            previous_kind = "number"
        else:
            text_tokens.append(token)
            previous_kind = "text"
        previous_match = match
    return (
        tuple(text_tokens),
        tuple(number_tokens),
        tuple(unit_tokens),
        tuple(strength_atoms),
        tuple(semantic_unit_tokens),
    )


def parse_product_search_query(value: object, *, mode: str = "manual") -> ProductSearchQuery:
    mode = str(mode or "manual").strip().lower()
    if mode not in _SEARCH_MODES:
        raise ValueError("search mode must be manual or ocr")
    original = str(value or "").strip()
    search_text = _strip_ocr_trailing_regimen(original) if mode == "ocr" else original
    normalized = _canonical_text(search_text)
    text_tokens, number_tokens, unit_tokens, strength_atoms, _semantic_units = (
        _scan_normalized_tokens(normalized)
    )
    return ProductSearchQuery(
        original=original,
        mode=mode,
        normalized=normalized,
        text_tokens=text_tokens,
        number_tokens=number_tokens,
        unit_tokens=unit_tokens,
        strength_atoms=strength_atoms,
    )


def _ordered_subsequence(needle: tuple[str, ...], haystack: tuple[str, ...]) -> bool:
    if not needle:
        return True
    cursor = 0
    for value in haystack:
        if value == needle[cursor]:
            cursor += 1
            if cursor == len(needle):
                return True
    return False


def _strength_alignment_penalty(
    needle: tuple[tuple[str, str | None], ...],
    haystack: tuple[tuple[str, str | None], ...],
) -> int | None:
    """Return how far ordered exact strengths are displaced in the candidate."""
    if not needle:
        return 0
    cursor = 0
    penalty = 0
    for candidate_index, (candidate_number, candidate_unit) in enumerate(haystack):
        query_number, query_unit = needle[cursor]
        if candidate_number != query_number:
            continue
        if query_unit is not None and candidate_unit != query_unit:
            continue
        penalty += candidate_index - cursor
        cursor += 1
        if cursor == len(needle):
            return penalty
    return None


def _edit_distance_at_most_one(left: str, right: str) -> int | None:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 1:
        return None
    if len(left) == len(right):
        mismatches = 0
        for a, b in zip(left, right):
            if a != b:
                mismatches += 1
                if mismatches > 1:
                    return None
        return 1
    if len(left) > len(right):
        left, right = right, left
    short_index = 0
    long_index = 0
    edits = 0
    while short_index < len(left) and long_index < len(right):
        if left[short_index] == right[long_index]:
            short_index += 1
            long_index += 1
            continue
        edits += 1
        if edits > 1:
            return None
        long_index += 1
    return 1


def _ordered_text_match(
    tokens: tuple[str, ...],
    candidate: str,
    *,
    allow_fuzzy: bool,
) -> tuple[bool, int]:
    cursor = 0
    edit_count = 0
    for token_index, token in enumerate(tokens):
        position = candidate.find(token, cursor)
        if position >= 0:
            cursor = position + len(token)
            continue
        if not allow_fuzzy or len(token) < 3:
            return False, edit_count
        if edit_count >= 1:
            return False, edit_count
        matched: tuple[int, int] | None = None
        starts = (0,) if token_index == 0 and cursor == 0 else range(cursor, len(candidate))
        for start in starts:
            for width in (len(token) - 1, len(token), len(token) + 1):
                if width < 1 or start + width > len(candidate):
                    continue
                distance = _edit_distance_at_most_one(token, candidate[start:start + width])
                if distance == 1:
                    matched = (start, width)
                    break
            if matched is not None:
                break
        if matched is None:
            return False, edit_count
        edit_count += 1
        cursor = matched[0] + matched[1]
    return True, edit_count


def _field_text(
    value: object,
) -> tuple[
    str,
    tuple[str, ...],
    tuple[tuple[str, str | None], ...],
]:
    text_tokens, _numbers, _literal_units, strength_atoms, semantic_units = _scan_normalized_tokens(
        _canonical_text(value)
    )
    return "".join(text_tokens), semantic_units, strength_atoms


def _match_text_field(
    query: ProductSearchQuery,
    value: object,
    *,
    field: str,
    field_rank: int,
) -> ProductSearchMatch | None:
    compact, field_units, field_strength_atoms = _field_text(value)
    if not compact or not query.text_tokens:
        return None
    strength_alignment = _strength_alignment_penalty(query.strength_atoms, field_strength_atoms)
    if strength_alignment is None:
        return None
    if not _ordered_subsequence(query.unit_tokens, field_units):
        return None
    matched, fuzzy_count = _ordered_text_match(
        query.text_tokens,
        compact,
        allow_fuzzy=query.mode == "ocr" and field == "product_name",
    )
    if not matched:
        return None
    query_compact = "".join(query.text_tokens)
    prefix = compact.startswith(query_compact) if not fuzzy_count else False
    gap = max(0, len(compact) - len(query_compact))
    tier = (
        "ocr_fuzzy"
        if fuzzy_count
        else "product_name_prefix" if field == "product_name" and prefix
        else "ordered_tokens"
    )
    return ProductSearchMatch(
        field=field,
        tier=tier,
        fuzzy=bool(fuzzy_count),
        sort_key=(field_rank, fuzzy_count, 0 if prefix else 1, strength_alignment, gap),
    )


def _match_ingredient_field(
    query: ProductSearchQuery,
    value: object,
) -> ProductSearchMatch | None:
    # A numeric/unit qualifier belongs to one top-level ingredient component.
    # Matching numbers against the whole field can bind another component's
    # strength to the queried ingredient (e.g. Glycerin borrowing 11% from a
    # preceding Pelargonium component).
    if query.number_tokens or query.unit_tokens:
        matches = [
            _match_text_field(query, component, field="ingredient_text", field_rank=1)
            for component in split_ingredient_components(value)
        ]
        found = [match for match in matches if match is not None]
        return min(found, key=lambda match: match.sort_key) if found else None
    return _match_text_field(query, value, field="ingredient_text", field_rank=1)


def match_product_fields(
    query: ProductSearchQuery,
    *,
    product_name: object,
    ingredient_text: object = None,
    manufacturer: object = None,
) -> ProductSearchMatch | None:
    product_match = _match_text_field(
        query,
        product_name,
        field="product_name",
        field_rank=0,
    )
    if product_match is not None:
        return product_match
    ingredient_match = _match_ingredient_field(query, ingredient_text)
    if ingredient_match is not None:
        return ingredient_match
    return _match_text_field(
        query,
        manufacturer,
        field="manufacturer",
        field_rank=2,
    )


def fuzzy_candidate_fragments(query: ProductSearchQuery) -> tuple[str, ...]:
    """Return small OCR-only text fragments used solely to generate candidates."""
    if query.mode != "ocr":
        return ()
    fragments: list[str] = []
    for token in sorted(query.text_tokens, key=len, reverse=True):
        if len(token) < 3:
            continue
        # Prefix/suffix fragments are deliberately non-overlapping. With a
        # single-character substitution, at least one fragment therefore
        # remains exact and can retrieve the row for the bounded fuzzy matcher.
        width = min(3, len(token) // 2)
        starts = (0, len(token) - width)
        for start in starts:
            fragment = token[start:start + width]
            if fragment and fragment not in fragments:
                fragments.append(fragment)
    return tuple(fragments[:6])


__all__ = [
    "ProductSearchMatch",
    "ProductSearchQuery",
    "fuzzy_candidate_fragments",
    "match_product_fields",
    "parse_product_search_query",
]
