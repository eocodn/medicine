from __future__ import annotations

import re
import unicodedata


_UNIT_SENTINEL = {
    "mg": "__unit_mg__",
    "ug": "__unit_ug__",
    "ml": "__unit_ml__",
    "g": "__unit_g__",
    "iu": "__unit_iu__",
    "pct": "__unit_pct__",
}
_ASCII_UNIT_PATTERNS = (
    (re.compile(r"(?:(?<=\d)(?:mcg|ug|μg)|(?<![a-z])(?:mcg|ug|μg)(?![a-z]))", re.IGNORECASE), "ug"),
    (re.compile(r"(?:(?<=\d)mg|(?<![a-z])mg(?![a-z]))", re.IGNORECASE), "mg"),
    (re.compile(r"(?:(?<=\d)ml|(?<![a-z])ml(?![a-z]))", re.IGNORECASE), "ml"),
    (re.compile(r"(?:(?<=\d)iu|(?<![a-z])iu(?![a-z]))", re.IGNORECASE), "iu"),
    (re.compile(r"(?:(?<=\d)g|(?<![a-z])g(?![a-z]))", re.IGNORECASE), "g"),
    (re.compile(r"%"), "pct"),
)
_KOREAN_UNIT_PATTERNS = (
    (re.compile(r"마이크로[ \t]*(?:그램|그람)", re.IGNORECASE), "ug"),
    (re.compile(r"밀리[ \t]*(?:그램|그람)", re.IGNORECASE), "mg"),
    (re.compile(r"밀리[ \t]*리터", re.IGNORECASE), "ml"),
    (re.compile(r"(?:(?<=\d)(?:그램|그람)|(?<![가-힣])(?:그램|그람)(?![가-힣]))"), "g"),
    (re.compile(r"(?:(?<=\d)아이유|(?<![가-힣])아이유(?![가-힣]))"), "iu"),
    (re.compile(r"(?:(?<=\d)단위|(?<![가-힣])단위(?![가-힣]))"), "iu"),
)
_NON_STRENGTH_ENUMERATION_RE = re.compile(r"[\u2460-\u249b\u24ea]")


def normalized_unit_text(value: object) -> str:
    raw = _NON_STRENGTH_ENUMERATION_RE.sub(" ", str(value or ""))
    text = unicodedata.normalize("NFKC", raw).casefold().replace("µ", "μ")
    for pattern, unit in _ASCII_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    for pattern, unit in _KOREAN_UNIT_PATTERNS:
        text = pattern.sub(f" {_UNIT_SENTINEL[unit]} ", text)
    return text


def canonical_search_text(value: object) -> str:
    """Canonical orthographic search text; bare digits retain no special meaning."""
    text = normalized_unit_text(value)
    for unit, sentinel in _UNIT_SENTINEL.items():
        text = text.replace(sentinel, unit)
    return "".join(char for char in text if char.isalnum())


def character_ngrams(value: object, size: int) -> tuple[str, ...]:
    if size < 1:
        raise ValueError("ngram size must be positive")
    text = canonical_search_text(value)
    if len(text) < size:
        return ()
    return tuple(dict.fromkeys(text[index:index + size] for index in range(len(text) - size + 1)))


def character_ngram_document(value: object, size: int) -> str:
    return " ".join(character_ngrams(value, size))


UNIT_SENTINEL = _UNIT_SENTINEL

__all__ = [
    "UNIT_SENTINEL",
    "canonical_search_text",
    "character_ngram_document",
    "character_ngrams",
    "normalized_unit_text",
]
