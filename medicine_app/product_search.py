from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


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
    (re.compile(r"(?<![a-z])(?:mcg|ug|μg)(?![a-z])", re.IGNORECASE), "ug"),
    (re.compile(r"(?<![a-z])mg(?![a-z])", re.IGNORECASE), "mg"),
    (re.compile(r"(?<![a-z])ml(?![a-z])", re.IGNORECASE), "ml"),
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
_ENCLOSED_ALPHANUMERIC_RE = re.compile(r"[\u2460-\u24ff]")
_COMPATIBILITY_RANGES = (
    (0x2070, 0x209F),  # superscripts/subscripts
    (0x2100, 0x214F),  # letterlike symbols
    (0x2150, 0x218F),  # number forms / Roman numerals
    (0x2460, 0x24FF),  # enclosed alphanumerics
    (0x3200, 0x33FF),  # enclosed CJK / compatibility units
    (0xFE30, 0xFE4F),  # CJK compatibility forms
    (0xFF00, 0xFFEF),  # halfwidth/fullwidth forms
)
_INGREDIENT_COMPONENT_SEPARATORS = frozenset(("/", "·", "ㆍ", "∙", "⋅"))
_BRACKET_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "<": ">",
    "（": "）",
    "［": "］",
    "｛": "｝",
    "〈": "〉",
}
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
    codepoint = ord(char)
    if not 0x2460 <= codepoint <= 0x24FF:
        return False
    normalized = unicodedata.normalize("NFKC", char)
    return any(value.isdigit() for value in normalized) and not any(
        value.isalpha() for value in normalized
    )


def _replace_enclosed_numeric_marker(match: re.Match[str]) -> str:
    char = match.group(0)
    return " " if _is_enclosed_numeric_marker(char) else char


def _canonical_text(value: object) -> str:
    raw = _ENCLOSED_ALPHANUMERIC_RE.sub(
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
    unicodedata.normalize("NFKC", chr(codepoint)).casefold(): chr(codepoint)
    for codepoint in range(0xFF01, 0xFF5F)
    if len(unicodedata.normalize("NFKC", chr(codepoint)).casefold()) == 1
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


def parse_product_search_query(value: object, *, mode: str = "manual") -> ProductSearchQuery:
    mode = str(mode or "manual").strip().lower()
    if mode not in _SEARCH_MODES:
        raise ValueError("search mode must be manual or ocr")
    original = str(value or "").strip()
    normalized = _canonical_text(original)
    if mode == "ocr":
        normalized = _OCR_TRAILING_REGIMEN_RE.sub(r"\1", normalized)
    text_tokens: list[str] = []
    number_tokens: list[str] = []
    unit_tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        unit = match.group(1)
        token = match.group(0).casefold()
        if unit:
            unit_tokens.append(unit.casefold())
        elif token[0].isdigit():
            number_tokens.append(_normalize_number(token))
        else:
            text_tokens.append(token)
    return ProductSearchQuery(
        original=original,
        mode=mode,
        normalized=normalized,
        text_tokens=tuple(text_tokens),
        number_tokens=tuple(number_tokens),
        unit_tokens=tuple(unit_tokens),
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


def _field_text(value: object) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    parsed = parse_product_search_query(value)
    return "".join(parsed.text_tokens), parsed.number_tokens, parsed.unit_tokens


def _match_text_field(
    query: ProductSearchQuery,
    value: object,
    *,
    field: str,
    field_rank: int,
) -> ProductSearchMatch | None:
    compact, field_numbers, field_units = _field_text(value)
    if not compact or not query.text_tokens:
        return None
    if not _ordered_subsequence(query.number_tokens, field_numbers):
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


def _ingredient_components(value: object) -> tuple[str, ...]:
    """Split canonical ingredient composition only at top-level separators.

    Canonical ingredient text uses slash and middle-dot forms between distinct
    components, but the same punctuation can occur inside parenthesized source
    descriptors. Numeric qualifiers must stay within one top-level component.
    """
    text = str(value or "")
    if not text:
        return ()
    components: list[str] = []
    current: list[str] = []
    closing_stack: list[str] = []
    for char in text:
        if char in _BRACKET_PAIRS:
            closing_stack.append(_BRACKET_PAIRS[char])
            current.append(char)
            continue
        if closing_stack and char == closing_stack[-1]:
            closing_stack.pop()
            current.append(char)
            continue
        if not closing_stack and char in _INGREDIENT_COMPONENT_SEPARATORS:
            component = "".join(current).strip()
            if component:
                components.append(component)
            current = []
            continue
        current.append(char)
    component = "".join(current).strip()
    if component:
        components.append(component)
    return tuple(components)


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
            for component in _ingredient_components(value)
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
    "raw_candidate_variants",
]
