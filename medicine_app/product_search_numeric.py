from __future__ import annotations

import re


_NUMBER_RE = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+")


def normalize_number(value: str) -> str | None:
    """Canonicalize decimal syntax used by explicit numeric qualifiers."""
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


__all__ = ["normalize_number"]
