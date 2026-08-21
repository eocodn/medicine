from __future__ import annotations


# Candidate SQL is a cheap prefilter, not a second Unicode normalization engine.
# We keep only spellings that are useful for the current medication catalog:
# ASCII case/fullwidth forms plus ordinary Hangul. Roman numeral letters are
# deliberately not used as required anchors because raw catalog text may use a
# single numeral glyph (e.g. FVⅢ -> fviii); `(주)` is likewise allowed to be raw
# `㈜`. Unsupported/ambiguous characters simply produce no SQL anchor and the
# authoritative normalized matcher decides after a broader candidate fetch.
_ROMAN_NUMERAL_ASCII = frozenset("ivxlcdm")
_FULLWIDTH_OFFSET = 0xFEE0


def _ascii_letter_pattern(char: str) -> tuple[tuple[str, str], ...]:
    if char in _ROMAN_NUMERAL_ASCII:
        return ()
    upper = char.upper()
    full_lower = chr(ord(char) + _FULLWIDTH_OFFSET)
    full_upper = chr(ord(upper) + _FULLWIDTH_OFFSET)
    return (("GLOB", f"*[{char}{upper}{full_lower}{full_upper}]*"),)


def _single_char_anchor_patterns(char: str) -> tuple[tuple[str, str], ...]:
    if "a" <= char <= "z":
        return _ascii_letter_pattern(char)
    if "가" <= char <= "힣":
        if char == "주":
            return ()
        return (("LIKE", f"%{char}%"),)
    return ()


def text_candidate_anchor_patterns(
    token: str,
    *,
    limit: int = 2,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Return up to two safe raw anchors for supported catalog spellings."""
    token = str(token or "").casefold()
    if not token or limit < 1:
        return ()
    candidates = [
        patterns
        for char in token
        if (patterns := _single_char_anchor_patterns(char))
    ]
    if not candidates:
        return ()
    if limit == 1 or len(candidates) == 1:
        return (candidates[0],)
    return (candidates[0], candidates[-1])


__all__ = ["text_candidate_anchor_patterns"]
