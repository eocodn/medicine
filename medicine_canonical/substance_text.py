from __future__ import annotations

import re
import unicodedata


def text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_substance_name(value: object) -> str:
    """Normalize only Unicode, case and whitespace for exact identity matching.

    Salts, hydrates, esters, strengths, punctuation and formulation words are
    deliberately preserved. Those differences require explicit evidence.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def split_top_level(value: object, separators: frozenset[str]) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return []
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    closing = {")": "(", "]": "[", "}": "{"}
    for char in text:
        if char in "([{":
            stack.append(char)
        elif char in closing and stack and stack[-1] == closing[char]:
            stack.pop()
        if not stack and char in separators:
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
        else:
            current.append(char)
    piece = "".join(current).strip()
    if piece:
        parts.append(piece)
    return parts


__all__ = ["normalize_substance_name", "split_top_level", "text_or_none"]