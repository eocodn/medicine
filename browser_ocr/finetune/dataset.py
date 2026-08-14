from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class DatasetError(ValueError):
    pass


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ORIGINS = {"synthetic", "real_deidentified", "public"}
_ALLOWED_DOCUMENT_TYPES = {"prescription", "medication_bag", "package", "other"}
_GROUP_KEYS = ("layout_family", "source_family", "drug_family")
_MANIFEST_KEYS = {
    "schema_version", "dataset_id", "task", "patient_data_policy", "samples_file",
    "description", "metadata",
}
_SAMPLE_KEYS = {
    "id", "image", "image_sha256", "text", "origin", "document_type", "document_id",
    "groups", "semantic_tags", "risk_tags", "privacy", "provenance",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_SPLIT_NAMES = ("train", "val", "test")


@dataclass(frozen=True)
class Dataset:
    root: Path
    manifest_path: Path
    manifest: dict
    samples: tuple[dict, ...]
    fingerprint: str


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _require_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must be an object")
    return value


def _require_id(value: object, label: str) -> str:
    text = str(value or "")
    if not _ID_RE.fullmatch(text):
        raise DatasetError(f"{label} must be a lowercase ASCII slug")
    return text


def _require_tag_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise DatasetError(f"{label} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _TAG_RE.fullmatch(item):
            raise DatasetError(f"{label} entries must be lowercase ASCII slugs")
        result.append(item)
    if len(set(result)) != len(result):
        raise DatasetError(f"{label} entries must be unique")
    return result


def _safe_relative(value: object, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{label} must be a relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DatasetError(f"{label} must stay inside the dataset root")
    absolute = (root / relative).resolve()
    resolved_root = root.resolve()
    if absolute != resolved_root and resolved_root not in absolute.parents:
        raise DatasetError(f"{label} escapes the dataset root")
    return absolute


def _validate_raster_signature(path: Path, sample_id: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(16)
    suffix = path.suffix.lower()
    valid = (
        (suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"))
        or (suffix == ".webp" and len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
        or (suffix == ".bmp" and header.startswith(b"BM"))
    )
    if not valid:
        raise DatasetError(f"{sample_id}.image header does not match its raster format")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_sample(sample: dict, root: Path, line_number: int) -> dict:
    unknown = sorted(set(sample) - _SAMPLE_KEYS)
    missing = sorted(_SAMPLE_KEYS - set(sample))
    if unknown:
        raise DatasetError(f"sample line {line_number} has unsupported fields: {', '.join(unknown)}")
    if missing:
        raise DatasetError(f"sample line {line_number} is missing fields: {', '.join(missing)}")

    sample_id = _require_id(sample["id"], f"sample line {line_number}.id")
    image = _safe_relative(sample["image"], root, f"{sample_id}.image")
    if image.suffix.lower() not in _IMAGE_SUFFIXES:
        raise DatasetError(f"{sample_id}.image uses an unsupported raster format")
    if not image.is_file():
        raise DatasetError(f"{sample_id}.image does not exist: {sample['image']}")
    _validate_raster_signature(image, sample_id)
    image_hash = str(sample["image_sha256"] or "")
    if not _HASH_RE.fullmatch(image_hash):
        raise DatasetError(f"{sample_id}.image_sha256 must be lowercase SHA-256")
    actual_hash = _sha256_file(image)
    if actual_hash != image_hash:
        raise DatasetError(f"{sample_id}.image_sha256 does not match the image")

    text = sample["text"]
    if not isinstance(text, str) or not text:
        raise DatasetError(f"{sample_id}.text must be a non-empty string")
    if any(char in text for char in ("\t", "\r", "\n", "\x00")):
        raise DatasetError(f"{sample_id}.text cannot contain tabs, line breaks, or NUL")
    if unicodedata.normalize("NFC", text) != text:
        raise DatasetError(f"{sample_id}.text must be NFC-normalized")

    origin = sample["origin"]
    if origin not in _ALLOWED_ORIGINS:
        raise DatasetError(f"{sample_id}.origin is unsupported")
    document_type = sample["document_type"]
    if document_type not in _ALLOWED_DOCUMENT_TYPES:
        raise DatasetError(f"{sample_id}.document_type is unsupported")
    document_id = _require_id(sample["document_id"], f"{sample_id}.document_id")

    groups = _require_object(sample["groups"], f"{sample_id}.groups")
    if set(groups) != set(_GROUP_KEYS):
        raise DatasetError(f"{sample_id}.groups must contain exactly {', '.join(_GROUP_KEYS)}")
    normalized_groups = {key: _require_id(groups[key], f"{sample_id}.groups.{key}") for key in _GROUP_KEYS}

    privacy = _require_object(sample["privacy"], f"{sample_id}.privacy")
    if set(privacy) != {"contains_patient_data", "deidentified"}:
        raise DatasetError(f"{sample_id}.privacy must contain contains_patient_data and deidentified")
    if privacy["contains_patient_data"] is not False:
        raise DatasetError(f"{sample_id} contains patient data; de-identify before dataset ingestion")
    if not isinstance(privacy["deidentified"], bool):
        raise DatasetError(f"{sample_id}.privacy.deidentified must be boolean")
    if origin == "real_deidentified" and privacy["deidentified"] is not True:
        raise DatasetError(f"{sample_id} real data must be explicitly deidentified")

    provenance = _require_object(sample["provenance"], f"{sample_id}.provenance")
    allowed_provenance = {"source_id", "license_id", "generator_version", "source_revision"}
    if set(provenance) - allowed_provenance:
        raise DatasetError(f"{sample_id}.provenance has unsupported fields")
    for key in ("source_id", "license_id"):
        value = provenance.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(c in value for c in "\r\n\t"):
            raise DatasetError(f"{sample_id}.provenance.{key} must be a short non-empty string")

    return {
        **sample,
        "id": sample_id,
        "image": Path(sample["image"]).as_posix(),
        "text": text,
        "origin": origin,
        "document_type": document_type,
        "document_id": document_id,
        "groups": normalized_groups,
        "semantic_tags": _require_tag_list(sample["semantic_tags"], f"{sample_id}.semantic_tags"),
        "risk_tags": _require_tag_list(sample["risk_tags"], f"{sample_id}.risk_tags"),
        "privacy": {"contains_patient_data": False, "deidentified": privacy["deidentified"]},
        "provenance": provenance,
    }


def load_dataset(
    manifest_path: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> Dataset:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise DatasetError(f"dataset manifest does not exist: {path}")
    try:
        manifest = _require_object(json.loads(path.read_text(encoding="utf-8")), "manifest")
    except json.JSONDecodeError as exc:
        raise DatasetError(f"invalid dataset manifest JSON: {exc}") from exc
    unknown = sorted(set(manifest) - _MANIFEST_KEYS)
    required = {"schema_version", "dataset_id", "task", "patient_data_policy", "samples_file"}
    missing = sorted(required - set(manifest))
    if unknown:
        raise DatasetError(f"manifest has unsupported fields: {', '.join(unknown)}")
    if missing:
        raise DatasetError(f"manifest is missing fields: {', '.join(missing)}")
    if manifest["schema_version"] != 1:
        raise DatasetError("unsupported fine-tuning dataset schema_version")
    _require_id(manifest["dataset_id"], "dataset_id")
    if manifest["task"] != "text_recognition":
        raise DatasetError("task must be text_recognition")
    if manifest["patient_data_policy"] != "forbid":
        raise DatasetError("patient_data_policy must be forbid")

    root = path.parent
    samples_path = _safe_relative(manifest["samples_file"], root, "samples_file")
    if not samples_path.is_file():
        raise DatasetError(f"samples_file does not exist: {manifest['samples_file']}")
    with samples_path.open("r", encoding="utf-8") as handle:
        total = sum(1 for _ in handle)
    if total == 0:
        raise DatasetError("dataset must contain at least one sample")

    samples: list[dict] = []
    sample_ids: set[str] = set()
    image_paths: set[str] = set()
    fingerprint = hashlib.sha256()
    fingerprint.update(_canonical_json(manifest))
    with samples_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            raw_line = raw_line.rstrip("\n")
            if raw_line.endswith("\r"):
                raw_line = raw_line[:-1]
            if not raw_line.strip():
                raise DatasetError(f"samples_file line {line_number} is blank")
            try:
                raw_sample = _require_object(json.loads(raw_line), f"sample line {line_number}")
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid JSON on samples_file line {line_number}: {exc}") from exc
            validated = _validate_sample(raw_sample, root, line_number)
            if validated["id"] in sample_ids:
                raise DatasetError(f"duplicate sample id: {validated['id']}")
            if validated["image"] in image_paths:
                raise DatasetError(f"duplicate image path: {validated['image']}")
            sample_ids.add(validated["id"])
            image_paths.add(validated["image"])
            samples.append(validated)
            fingerprint.update(b"\n")
            fingerprint.update(_canonical_json(validated))
            if progress and (line_number == total or line_number % 1000 == 0):
                progress(line_number, total)
    return Dataset(
        root=root,
        manifest_path=path,
        manifest=manifest,
        samples=tuple(samples),
        fingerprint=fingerprint.hexdigest(),
    )

def _script_flags(text: str) -> set[str]:
    flags: set[str] = set()
    for char in text:
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
            flags.add("korean")
        if ("A" <= char <= "Z") or ("a" <= char <= "z"):
            flags.add("latin")
        if char.isdigit():
            flags.add("digit")
    return flags


def dataset_stats(dataset: Dataset) -> dict:
    origins = Counter()
    document_types = Counter()
    scripts = Counter()
    semantic_tags = Counter()
    risk_tags = Counter()
    groups = {key: set() for key in _GROUP_KEYS}
    documents: set[str] = set()
    characters: set[str] = set()
    for sample in dataset.samples:
        origins[sample["origin"]] += 1
        document_types[sample["document_type"]] += 1
        documents.add(sample["document_id"])
        for key in _GROUP_KEYS:
            groups[key].add(sample["groups"][key])
        for script in _script_flags(sample["text"]):
            scripts[script] += 1
        semantic_tags.update(sample["semantic_tags"])
        risk_tags.update(sample["risk_tags"])
        characters.update(sample["text"])
    return {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "fingerprint": dataset.fingerprint,
        "sample_count": len(dataset.samples),
        "document_count": len(documents),
        "origins": dict(sorted(origins.items())),
        "document_types": dict(sorted(document_types.items())),
        "scripts": dict(sorted(scripts.items())),
        "semantic_tags": dict(sorted(semantic_tags.items())),
        "risk_tags": dict(sorted(risk_tags.items())),
        "group_counts": {key: len(groups[key]) for key in _GROUP_KEYS},
        "observed_character_count": len(characters),
    }


def _component_hash(seed: int, sample_ids: Iterable[str]) -> str:
    joined = "\0".join(sorted(sample_ids))
    return hashlib.sha256(f"{seed}\0{joined}".encode("utf-8")).hexdigest()


def _connected_components(dataset: Dataset, group_by: str) -> list[list[str]]:
    if group_by not in _GROUP_KEYS:
        raise DatasetError(f"group_by must be one of {', '.join(_GROUP_KEYS)}")
    union = _UnionFind()
    for sample in dataset.samples:
        document_node = f"document:{sample['document_id']}"
        group_node = f"{group_by}:{sample['groups'][group_by]}"
        union.union(document_node, group_node)
    samples_by_root: dict[str, list[str]] = defaultdict(list)
    for sample in dataset.samples:
        root = union.find(f"document:{sample['document_id']}")
        samples_by_root[root].append(sample["id"])
    return list(samples_by_root.values())


def build_split(
    dataset: Dataset,
    *,
    group_by: str,
    seed: int,
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
) -> dict:
    if not isinstance(seed, int):
        raise DatasetError("seed must be an integer")
    if len(ratios) != 3 or any(not isinstance(value, (int, float)) or value <= 0 for value in ratios):
        raise DatasetError("train/val/test ratios must all be positive")
    total_ratio = float(sum(ratios))
    if abs(total_ratio - 1.0) > 1e-9:
        raise DatasetError("train/val/test ratios must sum to 1")

    components = _connected_components(dataset, group_by)
    if len(components) < 3:
        raise DatasetError(
            f"{group_by} split has only {len(components)} connected components; at least 3 are required"
        )
    components.sort(key=lambda ids: (-len(ids), _component_hash(seed, ids)))
    total = len(dataset.samples)
    targets = {name: ratios[index] * total for index, name in enumerate(_SPLIT_NAMES)}
    assigned: dict[str, list[str]] = {name: [] for name in _SPLIT_NAMES}

    for index, component in enumerate(components):
        empty = [name for name in _SPLIT_NAMES if not assigned[name]]
        remaining = len(components) - index
        if empty and remaining <= len(empty):
            candidates = empty
        else:
            candidates = list(_SPLIT_NAMES)
        name = max(
            candidates,
            key=lambda split_name: (
                (targets[split_name] - len(assigned[split_name])) / max(targets[split_name], 1.0),
                -_SPLIT_NAMES.index(split_name),
            ),
        )
        assigned[name].extend(component)

    for name in _SPLIT_NAMES:
        assigned[name].sort()
        if not assigned[name]:
            raise DatasetError(f"deterministic split produced an empty {name} set")

    flattened = [sample_id for name in _SPLIT_NAMES for sample_id in assigned[name]]
    if len(flattened) != len(set(flattened)) or set(flattened) != {sample["id"] for sample in dataset.samples}:
        raise DatasetError("split assignment does not cover the dataset exactly once")

    return {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "group_by": group_by,
        "seed": seed,
        "ratios": {name: float(ratios[index]) for index, name in enumerate(_SPLIT_NAMES)},
        "component_count": len(components),
        "max_component_size": max(len(component) for component in components),
        "counts": {name: len(assigned[name]) for name in _SPLIT_NAMES},
        "splits": assigned,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_split(dataset: Dataset, split: dict) -> None:
    if split.get("schema_version") != 1:
        raise DatasetError("unsupported split schema_version")
    if split.get("dataset_id") != dataset.manifest["dataset_id"]:
        raise DatasetError("split dataset_id does not match the dataset")
    if split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("split dataset_fingerprint does not match the dataset")
    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != set(_SPLIT_NAMES):
        raise DatasetError("split must contain train, val and test")
    all_ids = []
    for name in _SPLIT_NAMES:
        if not isinstance(splits[name], list) or not splits[name]:
            raise DatasetError(f"split {name} must be a non-empty list")
        all_ids.extend(splits[name])
    expected = {sample["id"] for sample in dataset.samples}
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != expected:
        raise DatasetError("split must cover every sample exactly once")


def export_paddle(
    dataset: Dataset,
    split: dict,
    output_dir: str | Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    _validate_split(dataset, split)
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".{output.name}.stage-{uuid.uuid4().hex}"
    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
    stage.mkdir(parents=True)
    by_id = {sample["id"]: sample for sample in dataset.samples}
    total = len(dataset.samples)
    processed = 0
    try:
        for name in _SPLIT_NAMES:
            with (stage / f"{name}.txt").open("w", encoding="utf-8", newline="\n") as handle:
                for sample_id in split["splits"][name]:
                    sample = by_id[sample_id]
                    handle.write(f"{sample['image']}\t{sample['text']}\n")
                    processed += 1
                    if progress and (processed == total or processed % 1000 == 0):
                        progress(processed, total)
        observed = sorted({char for sample in dataset.samples for char in sample["text"] if char != " "})
        (stage / "observed-characters.txt").write_text("".join(f"{char}\n" for char in observed), encoding="utf-8")
        _write_json(stage / "split.json", split)
        report = {
            "schema_version": 1,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "sample_count": total,
            "counts": split["counts"],
            "group_by": split["group_by"],
            "seed": split["seed"],
            "data_dir": str(dataset.root),
            "label_files": {name: f"{name}.txt" for name in _SPLIT_NAMES},
            "observed_characters_file": "observed-characters.txt",
            "observed_character_count": len(observed),
        }
        _write_json(stage / "export.json", report)

        if output.exists():
            os.replace(output, backup)
        try:
            os.replace(stage, output)
        except Exception:
            if backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup.exists() and output.exists():
            shutil.rmtree(backup)
