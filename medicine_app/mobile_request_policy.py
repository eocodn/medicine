from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class MobileRequestPolicy:
    access: str
    requires_reference: bool = False


REFERENCE_READ = MobileRequestPolicy("reference")
REFERENCE_REQUIRED_READ = MobileRequestPolicy("reference", requires_reference=True)
PERSONAL_READ = MobileRequestPolicy("personal_read")
REFERENCE_REQUIRED_PERSONAL_READ = MobileRequestPolicy("personal_read", requires_reference=True)
PERSONAL_WRITE = MobileRequestPolicy("personal_write")
REFERENCE_REQUIRED_PERSONAL_WRITE = MobileRequestPolicy("personal_write", requires_reference=True)


def classify_mobile_request(method: str, raw_path: str) -> MobileRequestPolicy:
    normalized_method = method.upper().strip()
    path = urlsplit(raw_path).path

    if normalized_method == "GET" and path == "/api/health":
        return REFERENCE_READ
    if normalized_method == "GET" and path == "/api/products":
        return REFERENCE_REQUIRED_READ
    if normalized_method == "GET" and path == "/api/people":
        return PERSONAL_READ
    if normalized_method == "GET" and re.fullmatch(r"/api/medications/[^/]+/history", path):
        return PERSONAL_READ
    if normalized_method == "POST" and re.fullmatch(r"/api/people/[^/]+/medications/preview", path):
        return REFERENCE_REQUIRED_PERSONAL_READ
    if normalized_method == "POST" and re.fullmatch(r"/api/people/[^/]+/medications", path):
        return REFERENCE_REQUIRED_PERSONAL_WRITE
    if normalized_method == "PATCH" and re.fullmatch(r"/api/medications/[^/]+", path):
        return REFERENCE_REQUIRED_PERSONAL_WRITE
    # Unknown/new routes fail safe to the personal write boundary. Requiring
    # reference data is opt-in because local history/dose operations must keep
    # working while the signed reference is unavailable.
    return PERSONAL_WRITE


__all__ = ["MobileRequestPolicy", "classify_mobile_request"]