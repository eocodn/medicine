from __future__ import annotations

import re
import unicodedata


# Unicode 15.1 Nd blocks. Keeping the accepted decimal alphabet explicit lets
# the tokenizer and SQLite candidate GLOB share exactly the same semantics.
_DECIMAL_DIGIT_STARTS = (
    0x0030, 0x0660, 0x06F0, 0x07C0, 0x0966, 0x09E6, 0x0A66, 0x0AE6,
    0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66, 0x0DE6, 0x0E50, 0x0ED0,
    0x0F20, 0x1040, 0x1090, 0x17E0, 0x1810, 0x1946, 0x19D0, 0x1A80,
    0x1A90, 0x1B50, 0x1BB0, 0x1C40, 0x1C50, 0xA620, 0xA8D0, 0xA900,
    0xA9D0, 0xA9F0, 0xAA50, 0xABF0, 0xFF10, 0x104A0, 0x10D30, 0x11066,
    0x110F0, 0x11136, 0x111D0, 0x112F0, 0x11450, 0x114D0, 0x11650, 0x116C0,
    0x11730, 0x118E0, 0x11950, 0x11C50, 0x11D50, 0x11DA0, 0x11F50, 0x16A60,
    0x16AC0, 0x16B50, 0x1D7CE, 0x1D7D8, 0x1D7E2, 0x1D7EC, 0x1D7F6,
    0x1E140, 0x1E2F0, 0x1E4F0, 0x1E950, 0x1FBF0,
)
_NON_DECIMAL_PRESENTATION_RANGES = (
    (0x00B2, 0x00B3),  # superscript two/three
    (0x00B9, 0x00B9),  # superscript one
    (0x2070, 0x209F),  # remaining superscript/subscript digits
)
_DIRECT_NUMERIC_PUNCTUATION = {
    # Exhaustive one-codepoint Unicode 15.1 compatibility spellings whose
    # NFKC form is the ASCII decimal/grouping punctuation used by the matcher.
    ".": "․﹒．",
    ",": "︐﹐，",
}
# These are exactly the Unicode 15.1 compatibility forms whose NFKC output
# contains decimal digits but does not represent a medication-strength digit:
# fractions, enumeration labels, date/time labels, and dimension exponents.
_NON_STRENGTH_NUMERIC_COMPATIBILITY_RE = re.compile(
    "["
    "\u00bc-\u00be"
    "\u2150-\u215f\u2189"
    "\u2460-\u249b\u24ea"
    "\u3251-\u325f\u32b1-\u32cb"
    "\u3358-\u3370\u3378-\u3379\u339f-\u33a6\u33a8\u33af\u33e0-\u33fe"
    "\U0001f100-\U0001f10a"
    "]"
)


def _build_raw_decimal_map() -> dict[str, str]:
    values: dict[str, str] = {}
    for start in _DECIMAL_DIGIT_STARTS:
        for digit in range(10):
            values[chr(start + digit)] = str(digit)
    for start, end in _NON_DECIMAL_PRESENTATION_RANGES:
        for codepoint in range(start, end + 1):
            raw = chr(codepoint)
            normalized = unicodedata.normalize("NFKC", raw)
            if len(normalized) != 1 or normalized not in "0123456789":
                continue
            values[raw] = normalized
    return values


_RAW_DECIMAL_TO_ASCII = _build_raw_decimal_map()


def _build_direct_digit_compatibility() -> dict[str, str]:
    equivalents: dict[str, list[str]] = {str(value): [] for value in range(10)}
    for raw, normalized in _RAW_DECIMAL_TO_ASCII.items():
        if raw != normalized:
            equivalents[normalized].append(raw)
    return {digit: "".join(dict.fromkeys(values)) for digit, values in equivalents.items()}


_DIRECT_DIGIT_COMPATIBILITY = _build_direct_digit_compatibility()


def strip_non_strength_numeric_compatibility(value: str) -> str:
    """Remove compatibility metadata whose NFKC digits are not strengths."""
    return _NON_STRENGTH_NUMERIC_COMPATIBILITY_RE.sub(" ", str(value or ""))


def normalize_number(value: str) -> str | None:
    """Canonicalize one supported decimal token without Decimal context limits."""
    raw = str(value or "").replace(",", "")
    digits: list[str] = []
    for char in raw:
        if char == ".":
            digits.append(char)
            continue
        digit = _RAW_DECIMAL_TO_ASCII.get(char)
        if digit is None:
            return None
        digits.append(digit)
    raw = "".join(digits)
    if not raw or raw == "." or raw.count(".") > 1:
        return None
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
    if not token or not re.fullmatch(r"(?:[0-9]+(?:[.,][0-9]+)*|\.[0-9]+)", token):
        return None
    pieces: list[str] = []
    for char in token:
        variants = _DIRECT_DIGIT_COMPATIBILITY.get(char)
        if variants is None:
            variants = _DIRECT_NUMERIC_PUNCTUATION.get(char)
        pieces.append(f"[{char}{variants}]" if variants is not None else char)
    return "*" + "".join(pieces) + "*"


__all__ = [
    "normalize_number",
    "raw_numeric_compat_glob",
    "strip_non_strength_numeric_compatibility",
]