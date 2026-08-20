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
    r"__unit_(mg|ug|ml)__|\d+(?:\.\d+)?|[a-z]+|[가-힣]+",
    re.IGNORECASE,
)
_ASCII_UNIT_PATTERNS = (
    (re.compile(r"(?<![a-z가-힣])(?:mcg|ug|μg)(?![a-z가-힣])", re.IGNORECASE), "ug"),
    (re.compile(r"(?<![a-z가-힣])mg(?![a-z가-힣])", re.IGNORECASE), "mg"),
    (re.compile(r"(?<![a-z가-힣])ml(?![a-z가-힣])", re.IGNORECASE), "ml"),
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
        }


def _normalize_number(value: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if number == number.to_integral_value():
        return str(number.quantize(Decimal(1)))
    return format(number.normalize(), "f")


def _canonical_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("µ", "μ")
    for pattern, unit in _KOREAN_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    for pattern, unit in _ASCII_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    return text


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


def _distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        mismatches = 0
        for a, b in zip(left, right):
            if a != b:
                mismatches += 1
                if mismatches > 1:
                    return False
        return True
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
            return False
        long_index += 1
    return True


def _ordered_text_match(
    tokens: tuple[str, ...],
    candidate: str,
    *,
    allow_fuzzy: bool,
) -> tuple[bool, int]:
    cursor = 0
    fuzzy_count = 0
    for token in tokens:
        position = candidate.find(token, cursor)
        if position >= 0:
            cursor = position + len(token)
            continue
        if not allow_fuzzy or len(token) < 4:
            return False, fuzzy_count
        matched_position = None
        for start in range(cursor, max(cursor, len(candidate) - len(token) + 1)):
            window = candidate[start:start + len(token)]
            if len(window) == len(token) and _distance_at_most_one(token, window):
                matched_position = start
                break
        if matched_position is None:
            return False, fuzzy_count
        fuzzy_count += 1
        cursor = matched_position + len(token)
    return True, fuzzy_count


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
        sort_key=(field_rank, 1 if fuzzy_count else 0, 0 if prefix else 1, gap),
    )


def match_product_fields(
    query: ProductSearchQuery,
    *,
    product_name: object,
    ingredient_text: object = None,
    manufacturer: object = None,
) -> ProductSearchMatch | None:
    matches = [
        _match_text_field(
            query,
            product_name,
            field="product_name",
            field_rank=0,
        ),
        _match_text_field(
            query,
            ingredient_text,
            field="ingredient_text",
            field_rank=1,
        ),
        _match_text_field(
            query,
            manufacturer,
            field="manufacturer",
            field_rank=2,
        ),
    ]
    found = [match for match in matches if match is not None]
    return min(found, key=lambda match: match.sort_key) if found else None


def fuzzy_candidate_fragments(query: ProductSearchQuery) -> tuple[str, ...]:
    """Return small OCR-only text fragments used solely to generate candidates."""
    if query.mode != "ocr":
        return ()
    fragments: list[str] = []
    for token in sorted(query.text_tokens, key=len, reverse=True):
        if len(token) < 4:
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