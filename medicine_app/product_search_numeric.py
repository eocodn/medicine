from __future__ import annotations

import re


# Product/export names in the current catalog use enclosed digits as labels
# (for example 수출명①...②), not as dosage strengths. Strip that metadata before
# NFKC can turn it into an ordinary number. Do not grow this into a catalogue of
# every Unicode character that *can* normalize to digits: search intentionally
# supports medication/OCR number syntax, not Unicode numeric equivalence.
_NON_STRENGTH_ENUMERATION_RE = re.compile(r"[\u2460-\u249b\u24ea]")
_NUMBER_RE = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+")


def strip_non_strength_numeric_compatibility(value: str) -> str:
    """Remove observed enclosed-number labels before numeric tokenization."""
    return _NON_STRENGTH_ENUMERATION_RE.sub(" ", str(value or ""))


def normalize_number(value: str) -> str | None:
    """Canonicalize the decimal syntax used by medication strengths."""
    raw = str(value or "")
    if _NUMBER_RE.fullmatch(raw) is None:
        return None
    raw = raw.replace(",", "")
    if raw.startswith("."):
        raw = "0" + raw
    integer, dot, fraction = raw.partition(".")
    integer = integer.lstrip("0") or "0"
    if not dot:
        return integer
    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


__all__ = ["normalize_number", "strip_non_strength_numeric_compatibility"]
