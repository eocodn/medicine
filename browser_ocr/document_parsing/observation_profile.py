from __future__ import annotations

from typing import Any, Mapping

from .training_dataset import ParserDatasetError


def runtime_observation_profile(raw: object) -> dict[str, Any]:
    """Keep only detector/recognizer/orchestration identity for parser observations.

    A full-document result also pins the currently selected parser. Parser datasets
    must not inherit that identity: changing the parser must not invalidate an OCR
    observation produced by the same detector, cropper and recognizer.
    """

    if not isinstance(raw, Mapping):
        raise ParserDatasetError("runtime result profile must be an object")
    profile = {str(key): value for key, value in raw.items() if key != "parser"}
    implementation = profile.get("implementation")
    if isinstance(implementation, Mapping):
        profile["implementation"] = {
            str(key): value
            for key, value in implementation.items()
            if key not in {"parser", "parser_contract"}
        }
    return profile


__all__ = ["runtime_observation_profile"]
