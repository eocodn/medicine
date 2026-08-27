from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .artifact_storage import atomic_write, exclusive_output_lock
from .parser_v5_contract import validate_parser_v5_pair
from .parser_v5_observation import ObservationProfile, simulate_observations
from .parser_v5_world import ParserWorldProfile, generate_parser_world


DATASET_SCHEMA_VERSION = 1
SAMPLES_FILE = "samples.jsonl"
MANIFEST_FILE = "manifest.json"
_DATASET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_id",
    "task",
    "builder",
    "samples_file",
    "samples_sha256",
    "document_count",
    "generation",
}
_GENERATION_FIELDS = {"seed", "document_count", "world_profile", "observation_profile"}
_SAMPLE_FIELDS = {"sample_index", "truth", "observation"}


@dataclass(frozen=True)
class ParserV5Dataset:
    root: Path
    manifest_path: Path
    dataset_id: str
    generation: Mapping[str, Any]
    samples: tuple[dict[str, Any], ...]
    samples_sha256: str


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Parser v5 dataset value is not strict JSON: {exc}") from exc


def _canonical_json_line(value: object) -> bytes:
    return _canonical_json(value) + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_dataset_id(value: object) -> str:
    text = str(value or "").strip()
    if not _DATASET_ID_RE.fullmatch(text):
        raise ValueError("Parser v5 dataset_id must be a lowercase ASCII identifier")
    return text


def _profile_mapping(profile: ParserWorldProfile | ObservationProfile) -> dict[str, Any]:
    return asdict(profile)


def _generation_profile(
    *,
    seed: int,
    document_count: int,
    world_profile: ParserWorldProfile,
    observation_profile: ObservationProfile,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "document_count": document_count,
        "world_profile": _profile_mapping(world_profile),
        "observation_profile": _profile_mapping(observation_profile),
    }


def _validate_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _MANIFEST_FIELDS:
        raise ValueError("Parser v5 manifest fields are invalid")
    if value.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise ValueError("Parser v5 manifest schema_version must be 1")
    dataset_id = _require_dataset_id(value.get("dataset_id"))
    if value.get("task") != "medication_document_parser_v5":
        raise ValueError("Parser v5 manifest task is invalid")
    if value.get("builder") != "structured_world_observation_v1":
        raise ValueError("Parser v5 manifest builder is invalid")
    if value.get("samples_file") != SAMPLES_FILE:
        raise ValueError("Parser v5 manifest samples_file is invalid")
    samples_sha256 = str(value.get("samples_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", samples_sha256):
        raise ValueError("Parser v5 manifest samples_sha256 must be lowercase SHA-256")
    document_count = value.get("document_count")
    if isinstance(document_count, bool) or not isinstance(document_count, int) or document_count <= 0:
        raise ValueError("Parser v5 manifest document_count must be a positive integer")
    generation = value.get("generation")
    if not isinstance(generation, Mapping) or set(generation) != _GENERATION_FIELDS:
        raise ValueError("Parser v5 manifest generation profile is invalid")
    if generation.get("document_count") != document_count:
        raise ValueError("Parser v5 manifest generation document_count disagrees with manifest")
    seed = generation.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Parser v5 manifest generation seed must be an integer")
    _world_profile_from_mapping(generation.get("world_profile"))
    _observation_profile_from_mapping(generation.get("observation_profile"))
    return {
        **dict(value),
        "dataset_id": dataset_id,
        "generation": dict(generation),
    }


def _world_profile_from_mapping(value: object) -> ParserWorldProfile:
    if not isinstance(value, Mapping):
        raise ValueError("Parser v5 world profile must be an object")
    expected = set(asdict(ParserWorldProfile()))
    if set(value) != expected:
        raise ValueError("Parser v5 world profile fields are invalid")
    normalized = dict(value)
    for name in ("medication_count", "distractor_section_count"):
        raw = normalized[name]
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError(f"Parser v5 world profile {name} must contain two values")
        normalized[name] = tuple(raw)
    return ParserWorldProfile(**normalized)


def _observation_profile_from_mapping(value: object) -> ObservationProfile:
    if not isinstance(value, Mapping):
        raise ValueError("Parser v5 observation profile must be an object")
    expected = set(asdict(ObservationProfile()))
    if set(value) != expected:
        raise ValueError("Parser v5 observation profile fields are invalid")
    normalized = dict(value)
    raw_false_positives = normalized["false_positive_count"]
    if not isinstance(raw_false_positives, (list, tuple)) or len(raw_false_positives) != 2:
        raise ValueError("Parser v5 observation profile false_positive_count must contain two values")
    normalized["false_positive_count"] = tuple(raw_false_positives)
    return ObservationProfile(**normalized)


def _samples_bytes(samples: list[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json_line(sample) for sample in samples)


def build_parser_v5_dataset(
    output_dir: str | Path,
    *,
    dataset_id: str,
    document_count: int,
    seed: int,
    world_profile: ParserWorldProfile | None = None,
    observation_profile: ObservationProfile | None = None,
) -> Path:
    dataset_id = _require_dataset_id(dataset_id)
    if isinstance(document_count, bool) or not isinstance(document_count, int) or document_count <= 0:
        raise ValueError("Parser v5 document_count must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Parser v5 seed must be an integer")
    world = world_profile or ParserWorldProfile()
    observation = observation_profile or ObservationProfile()
    generation = _generation_profile(
        seed=seed,
        document_count=document_count,
        world_profile=world,
        observation_profile=observation,
    )
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / MANIFEST_FILE

    with exclusive_output_lock(root):
        if manifest_path.exists():
            existing = load_parser_v5_dataset(manifest_path)
            if existing.dataset_id == dataset_id and _canonical_json(existing.generation) == _canonical_json(generation):
                return manifest_path
            raise ValueError(f"{root} already contains a different Parser v5 dataset")

        samples: list[dict[str, Any]] = []
        for index in range(document_count):
            truth = generate_parser_world(seed=seed, document_index=index, profile=world)
            observed = simulate_observations(truth, seed=seed, profile=observation)
            validate_parser_v5_pair(truth, observed)
            samples.append({"sample_index": index, "truth": truth, "observation": observed})

        encoded_samples = _samples_bytes(samples)
        manifest = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "task": "medication_document_parser_v5",
            "builder": "structured_world_observation_v1",
            "samples_file": SAMPLES_FILE,
            "samples_sha256": _sha256(encoded_samples),
            "document_count": document_count,
            "generation": generation,
        }
        _validate_manifest(manifest)
        atomic_write(root / SAMPLES_FILE, encoded_samples)
        atomic_write(manifest_path, _canonical_json_line(manifest))
    return manifest_path


def load_parser_v5_dataset(manifest_path: str | Path) -> ParserV5Dataset:
    path = Path(manifest_path).resolve()
    try:
        raw_manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5 manifest: {path}") from exc
    manifest = _validate_manifest(raw_manifest)
    samples_path = path.parent / SAMPLES_FILE
    try:
        encoded_samples = samples_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read Parser v5 samples: {samples_path}") from exc
    actual_sha = _sha256(encoded_samples)
    if actual_sha != manifest["samples_sha256"]:
        raise ValueError("Parser v5 samples SHA-256 disagrees with manifest")

    samples: list[dict[str, Any]] = []
    document_ids: set[str] = set()
    for line_number, line in enumerate(encoded_samples.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Parser v5 sample line {line_number} is invalid JSON") from exc
        if not isinstance(sample, Mapping) or set(sample) != _SAMPLE_FIELDS:
            raise ValueError(f"Parser v5 sample line {line_number} fields are invalid")
        if sample.get("sample_index") != len(samples):
            raise ValueError("Parser v5 sample_index must be contiguous from zero")
        truth = sample.get("truth")
        observation = sample.get("observation")
        if not isinstance(truth, Mapping) or not isinstance(observation, Mapping):
            raise ValueError(f"Parser v5 sample line {line_number} truth and observation must be objects")
        validate_parser_v5_pair(truth, observation)
        document_id = str(truth["document_id"])
        if document_id in document_ids:
            raise ValueError("Parser v5 dataset document_ids must be unique")
        document_ids.add(document_id)
        samples.append(dict(sample))
    if len(samples) != manifest["document_count"]:
        raise ValueError("Parser v5 sample count disagrees with manifest")
    if _samples_bytes(samples) != encoded_samples:
        raise ValueError("Parser v5 samples must use canonical deterministic serialization")
    return ParserV5Dataset(
        root=path.parent,
        manifest_path=path,
        dataset_id=str(manifest["dataset_id"]),
        generation=dict(manifest["generation"]),
        samples=tuple(samples),
        samples_sha256=actual_sha,
    )


__all__ = [
    "ParserV5Dataset",
    "build_parser_v5_dataset",
    "load_parser_v5_dataset",
]