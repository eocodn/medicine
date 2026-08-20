from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .product_search_components import split_ingredient_components


_SEARCH_MODES = {"manual", "ocr"}
_UNIT_SENTINEL = {
    "mg": "__unit_mg__",
    "ug": "__unit_ug__",
    "ml": "__unit_ml__",
}
_TOKEN_RE = re.compile(
    r"__unit_(mg|ug|ml)__|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|[a-z]+|[가-힣]+",
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
    (re.compile(r"마이크로(?:그램|그람)", re.IGNORECASE), "ug"),
    (re.compile(r"밀리(?:그램|그람)", re.IGNORECASE), "mg"),
    (re.compile(r"밀리리터", re.IGNORECASE), "ml"),
)
_OCR_TRAILING_REGIMEN_RE = re.compile(
    r"(__unit_(?:mg|ug|ml)__)(?:\s*)(\d+(?:\.\d+)?)(?:\s*)(?:정|캡슐|포)\s*$",
    re.IGNORECASE,
)
_ENCLOSED_MARKER_CANDIDATE_RE = re.compile(
    r"[\u2460-\u24ff\u2776-\u2792\u3251-\u325f\u32b1-\u32bf\U0001f100]"
)
_COMPATIBILITY_RANGES = (
    (0x2070, 0x209F),  # superscripts/subscripts
    (0x2100, 0x214F),  # letterlike symbols
    (0x2150, 0x218F),  # number forms / Roman numerals
    (0x2460, 0x24FF),  # enclosed alphanumerics
    (0x3200, 0x33FF),  # enclosed CJK / compatibility units
    (0xFE30, 0xFE4F),  # CJK compatibility forms
    (0xFF00, 0xFFEF),  # halfwidth/fullwidth forms
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
    sort_key: tuple[int, int, int, int]

    def explanation(self) -> dict[str, object]:
        return {
            "field": self.field,
            "tier": self.tier,
            "fuzzy": self.fuzzy,
            "sort_key": list(self.sort_key),
        }


def _normalize_number(value: str) -> str:
    value = value.replace(",", "")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def _is_enclosed_numeric_marker(char: str) -> bool:
    normalized = unicodedata.normalize("NFKC", char)
    if not any(value.isdigit() for value in normalized) or any(
        value.isalpha() for value in normalized
    ):
        return False
    name = unicodedata.name(char, "")
    numeric_name = "DIGIT" in name or "NUMBER" in name
    enumeration_shape = any(
        marker in name
        for marker in ("CIRCLED", "PARENTHESIZED", "FULL STOP")
    )
    return numeric_name and enumeration_shape


def _replace_enclosed_numeric_marker(match: re.Match[str]) -> str:
    char = match.group(0)
    return " " if _is_enclosed_numeric_marker(char) else char


def _canonical_text(value: object) -> str:
    # The regex is a cheap prefilter over Unicode blocks that contain enclosed
    # numeric forms. Expensive Unicode semantic checks run only for those rare
    # characters, not for every character in every candidate row.
    raw = _ENCLOSED_MARKER_CANDIDATE_RE.sub(
        _replace_enclosed_numeric_marker,
        str(value or ""),
    )
    for symbol, unit_name in _RAW_UNIT_SYMBOLS.items():
        raw = raw.replace(symbol, f" {unit_name} ")
    text = unicodedata.normalize("NFKC", raw).casefold()
    text = text.replace("µ", "μ")
    for pattern, unit in _ASCII_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    for pattern, unit in _KOREAN_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    return text


def _single_compatibility_token(value: str) -> str | None:
    """Return the one search token represented by a compatibility glyph.

    Candidate SQL sees raw Contract-v1 text while the authoritative matcher
    sees NFKC-normalized text. Building this bounded reverse map from Unicode
    compatibility blocks lets candidate retrieval remain a superset of final
    matching without adding a database-side normalized index.
    """
    normalized = _canonical_text(value)
    matches = list(_TOKEN_RE.finditer(normalized))
    if len(matches) != 1:
        return None
    match = matches[0]
    unit = match.group(1)
    token = match.group(0).casefold()
    if unit:
        return unit.casefold()
    if token[0].isdigit():
        return _normalize_number(token)
    return token


def _build_compatibility_equivalents() -> dict[str, tuple[str, ...]]:
    equivalents: dict[str, list[str]] = {}
    for start, end in _COMPATIBILITY_RANGES:
        for codepoint in range(start, end + 1):
            raw = chr(codepoint)
            token = _single_compatibility_token(raw)
            if token is None or raw.casefold() == token:
                continue
            values = equivalents.setdefault(token, [])
            if raw not in values:
                values.append(raw)
    return {token: tuple(values) for token, values in equivalents.items()}


_COMPATIBILITY_EQUIVALENTS = _build_compatibility_equivalents()
_FULLWIDTH_BY_ASCII = {
    chr(codepoint): chr(codepoint + 0xFEE0)
    for codepoint in range(0x21, 0x7F)
}
_EMBEDDED_COMPATIBILITY_KEYS_BY_FIRST: dict[str, tuple[str, ...]] = {}
for _key in _COMPATIBILITY_EQUIVALENTS:
    if len(_key) < 2:
        continue
    _first = _key[0]
    _EMBEDDED_COMPATIBILITY_KEYS_BY_FIRST[_first] = tuple(sorted(
        (*_EMBEDDED_COMPATIBILITY_KEYS_BY_FIRST.get(_first, ()), _key),
        key=len,
        reverse=True,
    ))


def _embedded_compatibility_variants(token: str, *, limit: int = 24) -> tuple[str, ...]:
    """Expand compatibility glyphs that can replace a span inside one token."""
    variants: list[str] = []

    def walk(index: int, pieces: list[str], replaced: bool) -> None:
        if len(variants) >= limit:
            return
        if index >= len(token):
            candidate = "".join(pieces)
            if replaced and candidate != token and candidate not in variants:
                variants.append(candidate)
            return
        for key in _EMBEDDED_COMPATIBILITY_KEYS_BY_FIRST.get(token[index], ()):
            if not token.startswith(key, index):
                continue
            for raw in _COMPATIBILITY_EQUIVALENTS[key]:
                walk(index + len(key), [*pieces, raw], True)
                if len(variants) >= limit:
                    return
        walk(index + 1, [*pieces, token[index]], replaced)

    walk(0, [], False)
    return tuple(variants)


def raw_candidate_variants(
    token: str,
    *,
    include_fullwidth: bool = True,
) -> tuple[str, ...]:
    """Return bounded raw spellings that normalize to the same search token."""
    token = str(token or "").casefold()
    if not token:
        return ()
    variants = [token]
    if include_fullwidth:
        fullwidth = "".join(_FULLWIDTH_BY_ASCII.get(char, char) for char in token)
        if fullwidth != token:
            variants.append(fullwidth)
    is_number = bool(re.fullmatch(r"\d+(?:\.\d+)?", token))
    if not is_number:
        for raw in _COMPATIBILITY_EQUIVALENTS.get(token, ()):
            if raw not in variants:
                variants.append(raw)
        for raw in _embedded_compatibility_variants(token):
            if raw not in variants:
                variants.append(raw)
    return tuple(variants)


def raw_case_width_glob(token: str) -> str | None:
    """Match any ASCII/fullwidth upper/lower spelling without variant explosion."""
    token = str(token or "").casefold()
    if not token or not token.isascii() or not token.isalnum():
        return None
    pieces: list[str] = []
    for char in token:
        if "a" <= char <= "z":
            full_lower = _FULLWIDTH_BY_ASCII[char]
            full_upper = _FULLWIDTH_BY_ASCII[char.upper()]
            pieces.append(f"[{char}{char.upper()}{full_lower}{full_upper}]")
            continue
        if "0" <= char <= "9":
            pieces.append(f"[{char}{_FULLWIDTH_BY_ASCII[char]}]")
            continue
        return None
    return "*" + "".join(pieces) + "*"


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
    previous_kind: str | None = None
    for match in _TOKEN_RE.finditer(normalized):
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
        elif token[0].isdigit():
            number = _normalize_number(token)
            number_tokens.append(number)
            strength_atoms.append((number, None))
            number_spans.append(match.span())
            previous_kind = "number"
        else:
            text_tokens.append(token)
            previous_kind = "text"
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
    normalized = _canonical_text(original)
    if mode == "ocr":
        normalized = _OCR_TRAILING_REGIMEN_RE.sub(r"\1", normalized)
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


def _ordered_strength_atoms(
    needle: tuple[tuple[str, str | None], ...],
    haystack: tuple[tuple[str, str | None], ...],
) -> bool:
    """Match exact numbers in order while binding an explicit query unit to that number."""
    if not needle:
        return True
    cursor = 0
    for candidate_number, candidate_unit in haystack:
        query_number, query_unit = needle[cursor]
        if candidate_number != query_number:
            continue
        if query_unit is not None and candidate_unit != query_unit:
            continue
        cursor += 1
        if cursor == len(needle):
            return True
    return False


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
    if not _ordered_strength_atoms(query.strength_atoms, field_strength_atoms):
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
        sort_key=(field_rank, fuzzy_count, 0 if prefix else 1, gap),
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
    "raw_case_width_glob",
    "raw_candidate_variants",
]
