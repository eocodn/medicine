from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


_PSEUDONYMOUS_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_REAL_LICENSE_IDS = {"private-deidentified"}


def pseudonymous_token(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _PSEUDONYMOUS_TOKEN_RE.fullmatch(text):
        raise ValueError(f"{label} must be a pseudonymous lowercase ASCII token")
    return text


def real_license_id(value: object, label: str) -> str:
    text = pseudonymous_token(value, label)
    if text not in _REAL_LICENSE_IDS:
        raise ValueError(f"{label} must be a supported deidentified-source license id")
    return text


def normalized_provenance(value: object, *, source_kind: str, label: str) -> dict[str, str] | None:
    if value is None:
        if source_kind == "real_deidentified":
            raise ValueError(f"{label} is required for real_deidentified documents")
        return None
    if not isinstance(value, Mapping) or set(value) != {"source_id", "license_id"}:
        raise ValueError(f"{label} must contain source_id and license_id")
    source_id = pseudonymous_token(value.get("source_id"), f"{label}.source_id")
    license_id = (
        real_license_id(value.get("license_id"), f"{label}.license_id")
        if source_kind == "real_deidentified"
        else pseudonymous_token(value.get("license_id"), f"{label}.license_id")
    )
    return {"source_id": source_id, "license_id": license_id}


def normalized_polygon(
    value: object,
    *,
    label: str,
    width: int,
    height: int,
) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four points")
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label} points must contain x/y")
        converted: list[float] = []
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise ValueError(f"{label} coordinates must be numeric")
            number = float(coordinate)
            if not math.isfinite(number):
                raise ValueError(f"{label} coordinates must be finite")
            converted.append(number)
        x, y = converted
        if not 0.0 <= x <= float(width) or not 0.0 <= y <= float(height):
            raise ValueError(f"{label} must stay inside the document image")
        points.append(converted)

    twice_area = abs(sum(
        points[index][0] * points[(index + 1) % 4][1]
        - points[(index + 1) % 4][0] * points[index][1]
        for index in range(4)
    ))
    if twice_area <= 1e-6:
        raise ValueError(f"{label} must have non-zero area")
    return points


def require_unique_real_image_hashes(documents: Sequence[Mapping[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for document in documents:
        if document.get("source_kind") != "real_deidentified":
            continue
        image_sha256 = str(document.get("image_sha256") or "")
        document_id = str(document.get("document_id") or "")
        previous = seen.get(image_sha256)
        if previous is not None:
            raise ValueError(
                f"duplicate real image SHA-256 across parser documents: {previous} and {document_id}"
            )
        seen[image_sha256] = document_id


__all__ = [
    "normalized_polygon",
    "normalized_provenance",
    "pseudonymous_token",
    "real_license_id",
    "require_unique_real_image_hashes",
]
