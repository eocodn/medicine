from __future__ import annotations

from medicine_reference.mfds_sources import (
    MFDS_SOURCE_FAMILIES,
    MFDS_SOURCE_KEYS,
    MFDS_SOURCE_POLICY,
)

CANONICAL_SOURCE_POLICY = MFDS_SOURCE_POLICY

# Release/runtime allowlist for one complete canonical snapshot. Freshness is a
# separate update policy; this invariant only prevents partial or substituted sources.
EXPECTED_CANONICAL_SOURCE_FAMILIES = MFDS_SOURCE_FAMILIES
EXPECTED_CANONICAL_SOURCE_KEYS = MFDS_SOURCE_KEYS


__all__ = [
    "CANONICAL_SOURCE_POLICY",
    "EXPECTED_CANONICAL_SOURCE_FAMILIES",
    "EXPECTED_CANONICAL_SOURCE_KEYS",
]
