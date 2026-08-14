from __future__ import annotations

from collections import Counter
from pathlib import Path

from .dataset import Dataset, DatasetError


def _load_dictionary(path: str | Path, *, use_space_char: bool) -> set[str]:
    target = Path(path).resolve()
    if not target.is_file():
        raise DatasetError(f"character dictionary does not exist: {target}")
    characters = target.read_text(encoding="utf-8").splitlines()
    if not characters or any(not item for item in characters):
        raise DatasetError("character dictionary must contain one non-empty character per line")
    if len(characters) != len(set(characters)):
        raise DatasetError("character dictionary contains duplicate entries")
    result = set(characters)
    if use_space_char:
        result.add(" ")
    return result


def audit_model_compatibility(
    dataset: Dataset,
    dictionary_path: str | Path,
    *,
    max_text_length: int,
    use_space_char: bool,
) -> dict:
    if not isinstance(max_text_length, int) or max_text_length <= 0:
        raise DatasetError("max_text_length must be a positive integer")
    dictionary = _load_dictionary(dictionary_path, use_space_char=use_space_char)
    overlength: list[str] = []
    unknown = Counter()
    maximum = 0
    for sample in dataset.samples:
        text = sample["text"]
        maximum = max(maximum, len(text))
        if len(text) > max_text_length:
            overlength.append(sample["id"])
        for character in text:
            if character not in dictionary:
                unknown[character] += 1
    return {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "max_text_length": max_text_length,
        "observed_max_text_length": maximum,
        "overlength_sample_count": len(overlength),
        "overlength_sample_ids": overlength[:100],
        "unknown_character_count": len(unknown),
        "unknown_characters": dict(sorted(unknown.items())),
        "status": "ok" if not overlength and not unknown else "incompatible",
    }
