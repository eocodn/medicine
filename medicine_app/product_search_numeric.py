from __future__ import annotations

import re
import unicodedata


_DIRECT_DIGIT_RANGES = (
    (0x00B2, 0x00B3),  # superscript two/three
    (0x00B9, 0x00B9),  # superscript one
    (0x2070, 0x209F),  # superscript/subscript digits
    (0xFF10, 0xFF19),  # fullwidth digits
    (0x1D7CE, 0x1D7FF),  # mathematical presentation digits
    (0x1FBF0, 0x1FBF9),  # segmented presentation digits
)
_DIRECT_NUMERIC_PUNCTUATION = {
    ".": "．",
    ",": "，",
}


def _build_direct_digit_compatibility() -> dict[str, str]:
    equivalents: dict[str, list[str]] = {str(value): [] for value in range(10)}
    for start, end in _DIRECT_DIGIT_RANGES:
        for codepoint in range(start, end + 1):
            raw = chr(codepoint)
            normalized = unicodedata.normalize("NFKC", raw)
            if len(normalized) != 1 or normalized not in equivalents or raw == normalized:
                continue
            equivalents[normalized].append(raw)
    return {digit: "".join(dict.fromkeys(values)) for digit, values in equivalents.items()}


_DIRECT_DIGIT_COMPATIBILITY = _build_direct_digit_compatibility()


def normalize_number(value: str) -> str:
    """Canonicalize a decimal token without Decimal context limits."""
    raw = str(value or "").replace(",", "")
    digits: list[str] = []
    for char in raw:
        if char == ".":
            digits.append(char)
            continue
        try:
            digits.append(str(unicodedata.decimal(char)))
        except (TypeError, ValueError):
            digits.append(char)
    raw = "".join(digits)
    if raw.startswith("."):
        raw = "0" + raw
    integer, dot, fraction = raw.partition(".")
    integer = integer.lstrip("0") or "0"
    if not dot:
        return integer
    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


def raw_numeric_compat_glob(token: str) -> str | None:
    """Match direct digit compatibility forms while excluding enumeration glyphs."""
    token = str(token or "")
    if not token or not re.fullmatch(r"(?:\d+(?:[.,]\d+)*|\.\d+)", token):
        return None
    pieces: list[str] = []
    for char in token:
        variants = _DIRECT_DIGIT_COMPATIBILITY.get(char)
        if variants is None:
            variants = _DIRECT_NUMERIC_PUNCTUATION.get(char)
        pieces.append(f"[{char}{variants}]" if variants is not None else char)
    return "*" + "".join(pieces) + "*"


__all__ = ["normalize_number", "raw_numeric_compat_glob"]