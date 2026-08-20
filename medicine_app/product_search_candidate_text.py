from __future__ import annotations

import re
import unicodedata


# These blocks contain the compatibility/case forms that are relevant to
# product text (Latin presentation forms, Roman numerals, enclosed letters,
# mathematical letters, and fullwidth forms). Candidate anchors deliberately
# over-approximate them; the authoritative matcher still decides correctness.
_TEXT_EQUIVALENT_RANGES = (
    (0x00A0, 0x02FF),
    (0x1D00, 0x1D7F),
    (0x2070, 0x218F),
    (0x2460, 0x24FF),
    (0x3200, 0x33FF),
    (0xFB00, 0xFB4F),
    (0xFF00, 0xFFEF),
    (0x1D400, 0x1D7FF),
    (0x1F100, 0x1F1FF),
)


def _normalized_text_key(raw: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", raw).casefold()
    tokens = re.findall(r"[a-z]+|[가-힣]+", normalized)
    return tokens[0] if len(tokens) == 1 else None


def _build_raw_text_equivalents() -> dict[str, tuple[str, ...]]:
    equivalents: dict[str, list[str]] = {}
    for start, end in _TEXT_EQUIVALENT_RANGES:
        for codepoint in range(start, end + 1):
            raw = chr(codepoint)
            key = _normalized_text_key(raw)
            if key is None:
                continue
            values = equivalents.setdefault(key, [])
            if raw not in values:
                values.append(raw)
    return {key: tuple(values) for key, values in equivalents.items()}


_RAW_TEXT_EQUIVALENTS = _build_raw_text_equivalents()
_MULTI_TEXT_KEYS_BY_FIRST: dict[str, tuple[str, ...]] = {}
for _key in _RAW_TEXT_EQUIVALENTS:
    if len(_key) < 2:
        continue
    _first = _key[0]
    _MULTI_TEXT_KEYS_BY_FIRST[_first] = tuple(sorted(
        (*_MULTI_TEXT_KEYS_BY_FIRST.get(_first, ()), _key),
        key=len,
        reverse=True,
    ))


def _build_jamo_raw_equivalents() -> dict[str, tuple[str, ...]]:
    equivalents: dict[str, list[str]] = {}
    for start, end in ((0x3130, 0x318F), (0xFFA0, 0xFFDC)):
        for codepoint in range(start, end + 1):
            raw = chr(codepoint)
            normalized = unicodedata.normalize("NFKC", raw)
            if len(normalized) != 1 or not 0x1100 <= ord(normalized) <= 0x11FF:
                continue
            values = equivalents.setdefault(normalized, [])
            if raw not in values:
                values.append(raw)
    return {key: tuple(values) for key, values in equivalents.items()}


_JAMO_RAW_EQUIVALENTS = _build_jamo_raw_equivalents()


def _glob_class(values: tuple[str, ...]) -> str | None:
    if not values or any(len(value) != 1 or value in "[]-^" for value in values):
        return None
    return "[" + "".join(dict.fromkeys(values)) + "]"


def _hangul_anchor_patterns(char: str) -> tuple[tuple[str, str], ...]:
    raw_chars = (char, *_RAW_TEXT_EQUIVALENTS.get(char, ()))
    char_class = _glob_class(raw_chars)
    patterns: list[tuple[str, str]] = (
        [("GLOB", f"*{char_class}*")]
        if char_class
        else [("LIKE", f"%{raw}%") for raw in raw_chars]
    )
    decomposed = unicodedata.normalize("NFD", char)
    if decomposed != char:
        classes = [
            _glob_class((jamo, *_JAMO_RAW_EQUIVALENTS.get(jamo, ())))
            for jamo in decomposed
        ]
        if all(classes):
            patterns.append(("GLOB", "*" + "".join(classes) + "*"))
    return tuple(patterns)


def _single_char_anchor_patterns(char: str) -> tuple[tuple[str, str], ...]:
    if "a" <= char <= "z":
        variants = [char, char.upper()]
        for raw in _RAW_TEXT_EQUIVALENTS.get(char, ()):
            if raw not in variants:
                variants.append(raw)
        char_class = _glob_class(tuple(variants))
        if char_class:
            return (("GLOB", f"*{char_class}*"),)
        return tuple(("LIKE", f"%{variant}%") for variant in variants)
    if "가" <= char <= "힣":
        return _hangul_anchor_patterns(char)
    return ()


def text_candidate_anchor_patterns(
    token: str,
    *,
    limit: int = 2,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Return bounded raw anchors that every supported spelling must retain.

    One raw compatibility glyph may normalize to several query characters
    (for example Ⅱ -> ``ii`` or ß -> ``ss``). Positions covered by any such
    span cannot be required independently, so they are skipped. Remaining
    characters are represented by all supported one-codepoint compatibility
    forms; Hangul additionally includes canonical/compatibility Jamo spellings.
    Requiring only these anchors deliberately broadens SQL candidates while
    final normalized matching remains authoritative.
    """
    token = str(token or "").casefold()
    if not token or limit < 1:
        return ()

    covered = [False] * len(token)
    for index, char in enumerate(token):
        for key in _MULTI_TEXT_KEYS_BY_FIRST.get(char, ()):
            if token.startswith(key, index):
                for covered_index in range(index, min(len(token), index + len(key))):
                    covered[covered_index] = True

    candidates: list[tuple[int, tuple[tuple[str, str], ...]]] = []
    for index, char in enumerate(token):
        if covered[index]:
            continue
        patterns = _single_char_anchor_patterns(char)
        if patterns:
            candidates.append((index, patterns))
    if not candidates:
        return ()
    if len(candidates) == 1 or limit == 1:
        return (candidates[0][1],)
    return (candidates[0][1], candidates[-1][1])


__all__ = ["text_candidate_anchor_patterns"]