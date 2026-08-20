from __future__ import annotations

import re


_DIRECT_DIGIT_COMPATIBILITY = {
    "0": "０⁰₀",
    "1": "１¹₁",
    "2": "２²₂",
    "3": "３³₃",
    "4": "４⁴₄",
    "5": "５⁵₅",
    "6": "６⁶₆",
    "7": "７⁷₇",
    "8": "８⁸₈",
    "9": "９⁹₉",
}
_DIRECT_NUMERIC_PUNCTUATION = {
    ".": "．",
    ",": "，",
}


def raw_numeric_compat_glob(token: str) -> str | None:
    """Match direct digit compatibility forms while excluding enumeration glyphs."""
    token = str(token or "")
    if not token or not re.fullmatch(r"\d+(?:[.,]\d+)*", token):
        return None
    pieces: list[str] = []
    for char in token:
        variants = _DIRECT_DIGIT_COMPATIBILITY.get(char)
        if variants is None:
            variants = _DIRECT_NUMERIC_PUNCTUATION.get(char)
        pieces.append(f"[{char}{variants}]" if variants is not None else char)
    return "*" + "".join(pieces) + "*"


__all__ = ["raw_numeric_compat_glob"]