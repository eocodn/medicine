from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .artifact_invariants import real_license_id
from .observation_profile import runtime_observation_profile
from .training_alignment import build_relation_labels
from .training_dataset import ParserDatasetError, normalize_parser_documents


_ALLOWED_SPLITS = {"val", "test"}
_ALLOWED_DOCUMENT_TYPES = {"prescription", "medication_bag"}
_SHA256_LENGTH = 64
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
REAL_PARSER_LOCK_FILE = ".real-parser.lock"


@dataclass(frozen=True)
class RealSourceDataset:
    root: Path
    manifest_path: Path
    samples_path: Path
    dataset_id: str
    samples: tuple[dict[str, Any], ...]


def _require_slug(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise ParserDatasetError(f"{label} must be a pseudonymous lowercase ASCII slug")
    return text


def _require_tags(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ParserDatasetError(f"{label} must be a list")
    tags: list[str] = []
    for raw in value:
        tag = str(raw or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise ParserDatasetError(f"{label} entries must be lowercase ASCII tags")
        if tag in tags:
            raise ParserDatasetError(f"{label} entries must be unique")
        tags.append(tag)
    return tags


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def annotation_immutable_sha256(annotation: Mapping[str, Any]) -> str:
    observation = annotation.get("observation")
    if not isinstance(observation, Mapping):
        raise ParserDatasetError("real annotation observation must be an object")
    nodes = observation.get("nodes")
    if not isinstance(nodes, list):
        raise ParserDatasetError("real annotation observation nodes must be a list")
    immutable_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ParserDatasetError("real annotation observation node must be an object")
        immutable_nodes.append({
            "node_id": node.get("node_id"),
            "text": node.get("text"),
            "confidence": node.get("confidence"),
            "polygon": node.get("polygon"),
            "target_region_ids": node.get("target_region_ids"),
        })
    payload = {
        "document_id": annotation.get("document_id"),
        "split": annotation.get("split"),
        "source_kind": annotation.get("source_kind"),
        "image_sha256": annotation.get("image_sha256"),
        "width": annotation.get("width"),
        "height": annotation.get("height"),
        "layout_family": annotation.get("layout_family"),
        "scenario_tags": annotation.get("scenario_tags"),
        "risk_tags": annotation.get("risk_tags"),
        "privacy": annotation.get("privacy"),
        "provenance": annotation.get("provenance"),
        "source_binding": annotation.get("source_binding"),
        "observation": {
            "kind": observation.get("kind"),
            "profile": observation.get("profile"),
            "nodes": immutable_nodes,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_raster_signature(path: Path, label: str) -> None:
    header = path.read_bytes()[:16]
    suffix = path.suffix.lower()
    valid = (
        (suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"))
        or (suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix == ".webp" and len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP")
    )
    if not valid:
        raise ParserDatasetError(f"{label}.image header does not match its raster format")


def _safe_image(root: Path, raw: object, label: str) -> tuple[str, Path]:
    value = str(raw or "")
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        raise ParserDatasetError(f"{label}.image must be a relative path inside the real dataset root")
    absolute = (root / relative).resolve()
    if root != absolute and root not in absolute.parents:
        raise ParserDatasetError(f"{label}.image escapes the real dataset root")
    if not absolute.is_file():
        raise ParserDatasetError(f"{label}.image does not exist: {value}")
    if absolute.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ParserDatasetError(f"{label}.image uses an unsupported raster format")
    _validate_raster_signature(absolute, label)
    return relative.as_posix(), absolute


def load_real_source_manifest(manifest_path: str | Path) -> RealSourceDataset:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ParserDatasetError(f"real source manifest does not exist: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParserDatasetError(f"real source manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ParserDatasetError("real source manifest must be an object")
    if set(manifest) != {"schema_version", "dataset_id", "source_kind", "patient_data_policy", "samples_file"}:
        raise ParserDatasetError("real source manifest fields do not match schema v1")
    if manifest.get("schema_version") != 1:
        raise ParserDatasetError("unsupported real source schema_version")
    if manifest.get("source_kind") != "real_deidentified":
        raise ParserDatasetError("real source_kind must be real_deidentified")
    if manifest.get("patient_data_policy") != "forbid":
        raise ParserDatasetError("real patient_data_policy must be forbid")
    dataset_id = _require_slug(manifest.get("dataset_id"), "real dataset_id")
    samples_relative = Path(str(manifest.get("samples_file") or ""))
    if samples_relative.is_absolute() or ".." in samples_relative.parts:
        raise ParserDatasetError("real samples_file must stay inside the dataset root")
    samples_path = (path.parent / samples_relative).resolve()
    if not samples_path.is_file():
        raise ParserDatasetError("real samples_file does not exist")

    source_binding = {
        "kind": "real_source",
        "source_dataset_id": dataset_id,
        "source_manifest_sha256": _sha256_file(path),
        "source_samples_sha256": _sha256_file(samples_path),
    }
    samples: list[dict[str, Any]] = []
    ids: set[str] = set()
    image_hashes: dict[str, str] = {}
    for line_number, line in enumerate(samples_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ParserDatasetError(f"real samples line {line_number} is invalid JSON") from exc
        if not isinstance(raw, dict):
            raise ParserDatasetError(f"real samples line {line_number} must be an object")
        required = {
            "document_id", "image", "image_sha256", "split", "document_type", "layout_family",
            "privacy", "provenance", "scenario_tags", "risk_tags",
        }
        if set(raw) != required:
            raise ParserDatasetError(f"real sample line {line_number} fields do not match schema v1")
        document_id = _require_slug(raw.get("document_id"), "real document_id")
        if document_id in ids:
            raise ParserDatasetError("real document_id values must be unique")
        ids.add(document_id)
        split = str(raw.get("split") or "")
        if split not in _ALLOWED_SPLITS:
            raise ParserDatasetError(f"{document_id} real data is holdout-only and split must be val or test")
        document_type = str(raw.get("document_type") or "")
        if document_type not in _ALLOWED_DOCUMENT_TYPES:
            raise ParserDatasetError(f"{document_id}.document_type is unsupported")
        privacy = raw.get("privacy")
        if not isinstance(privacy, dict) or set(privacy) != {"contains_patient_data", "deidentified"}:
            raise ParserDatasetError(f"{document_id}.privacy must be explicit")
        if privacy.get("contains_patient_data") is not False or privacy.get("deidentified") is not True:
            raise ParserDatasetError(f"{document_id} must be deidentified before ingestion")
        image_relative, image_path = _safe_image(path.parent, raw.get("image"), document_id)
        if Path(image_relative).stem != document_id:
            raise ParserDatasetError(f"{document_id}.image filename must use the pseudonymous document_id")
        expected_sha = str(raw.get("image_sha256") or "")
        if len(expected_sha) != _SHA256_LENGTH or _sha256_file(image_path) != expected_sha:
            raise ParserDatasetError(f"{document_id}.image SHA-256 mismatch")
        previous_document = image_hashes.get(expected_sha)
        if previous_document is not None:
            raise ParserDatasetError(
                f"real source contains duplicate image SHA-256 for {previous_document} and {document_id}"
            )
        image_hashes[expected_sha] = document_id
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict) or set(provenance) != {"source_id", "license_id"}:
            raise ParserDatasetError(f"{document_id}.provenance requires exactly source_id and license_id")
        normalized_provenance = {
            "source_id": _require_slug(provenance.get("source_id"), f"{document_id}.provenance.source_id"),
            "license_id": "",
        }
        try:
            normalized_provenance["license_id"] = real_license_id(
                provenance.get("license_id"),
                f"{document_id}.provenance.license_id",
            )
        except ValueError as exc:
            raise ParserDatasetError(str(exc)) from exc
        layout_family = _require_slug(raw.get("layout_family"), f"{document_id}.layout_family")
        samples.append({
            **raw,
            "image": image_relative,
            "layout_family": layout_family,
            "scenario_tags": _require_tags(raw.get("scenario_tags"), f"{document_id}.scenario_tags"),
            "risk_tags": _require_tags(raw.get("risk_tags"), f"{document_id}.risk_tags"),
            "provenance": normalized_provenance,
            "source_binding": dict(source_binding),
        })
    if not samples:
        raise ParserDatasetError("real source manifest has no samples")
    return RealSourceDataset(
        root=path.parent,
        manifest_path=path,
        samples_path=samples_path,
        dataset_id=dataset_id,
        samples=tuple(samples),
    )


def _runtime_nodes(regions: object) -> list[dict[str, Any]]:
    if not isinstance(regions, list):
        raise ParserDatasetError("runtime OCR result regions must be a list")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fallback_index, raw in enumerate(regions, start=1):
        if not isinstance(raw, Mapping):
            raise ParserDatasetError("runtime OCR region must be an object")
        raw_index = raw.get("index", fallback_index)
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index <= 0:
            raise ParserDatasetError("runtime OCR region index must be positive")
        node_id = f"region-{raw_index:04d}"
        if node_id in seen:
            raise ParserDatasetError(f"duplicate runtime OCR region index: {raw_index}")
        seen.add(node_id)
        score = raw.get("recognition_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ParserDatasetError(f"{node_id}.recognition_score must be numeric")
        nodes.append(
            {
                "node_id": node_id,
                "text": str(raw.get("text") or ""),
                "confidence": float(score),
                "polygon": raw.get("polygon"),
                "target_region_ids": [],
                "label_status": "unlabeled",
                "semantic_role": None,
                "association_group": None,
            }
        )
    nodes.sort(key=lambda node: node["node_id"])
    return nodes


def prepare_real_annotation(sample: Mapping[str, Any], runtime_result: Mapping[str, Any]) -> dict[str, Any]:
    if runtime_result.get("status") != "ok":
        raise ParserDatasetError("runtime OCR result is not successful")
    image = runtime_result.get("image")
    if not isinstance(image, Mapping):
        raise ParserDatasetError("runtime OCR result is missing image metadata")
    if str(image.get("sha256") or "") != str(sample.get("image_sha256") or ""):
        raise ParserDatasetError("runtime OCR result image SHA-256 does not match real source")
    width = image.get("width")
    height = image.get("height")
    source_width = image.get("source_width")
    source_height = image.get("source_height")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ParserDatasetError("runtime OCR image width is invalid")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise ParserDatasetError("runtime OCR image height is invalid")
    if isinstance(source_width, bool) or not isinstance(source_width, int) or source_width <= 0:
        raise ParserDatasetError("runtime OCR source image width is invalid")
    if isinstance(source_height, bool) or not isinstance(source_height, int) or source_height <= 0:
        raise ParserDatasetError("runtime OCR source image height is invalid")
    stages = runtime_result.get("stages")
    orientation = stages.get("orientation") if isinstance(stages, Mapping) else None
    rotation = orientation.get("applied_rotation_degrees") if isinstance(orientation, Mapping) else None
    if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in {0, 90, 180, 270}:
        raise ParserDatasetError("runtime OCR orientation metadata is invalid")
    expected = (source_height, source_width) if rotation in {90, 270} else (source_width, source_height)
    if (width, height) != expected:
        raise ParserDatasetError("runtime OCR canonical dimensions disagree with orientation")
    profile = runtime_result.get("profile")
    if not isinstance(profile, Mapping):
        raise ParserDatasetError("runtime OCR result profile must be an object")
    return {
        "document_id": str(sample["document_id"]),
        "split": str(sample["split"]),
        "source_kind": "real_deidentified",
        "image_sha256": str(sample["image_sha256"]),
        "width": width,
        "height": height,
        "layout_family": str(sample["layout_family"]),
        "scenario_tags": list(sample["scenario_tags"]),
        "risk_tags": list(sample["risk_tags"]),
        "privacy": {"contains_patient_data": False, "deidentified": True},
        "provenance": dict(sample["provenance"]),
        "source_binding": dict(sample["source_binding"]),
        "observation": {
            "kind": "runtime_ocr",
            "profile": runtime_observation_profile(
                profile,
                expected_image_sha256=str(sample["image_sha256"]),
            ),
            "nodes": _runtime_nodes(runtime_result.get("regions")),
        },
        "relations": [],
        "gold_rows": [],
        "gold_rows_reviewed": False,
        "annotation_status": "draft",
    }


def finalize_real_annotation(
    annotation: Mapping[str, Any],
    *,
    expected_immutable_sha256: str,
) -> dict[str, Any]:
    if annotation_immutable_sha256(annotation) != expected_immutable_sha256:
        raise ParserDatasetError("real annotation immutable snapshot SHA-256 mismatch")
    document = dict(annotation)
    observation = document.get("observation")
    if not isinstance(observation, Mapping) or observation.get("kind") != "runtime_ocr":
        raise ParserDatasetError("real annotation must contain runtime_ocr observation")
    nodes = observation.get("nodes")
    if not isinstance(nodes, list):
        raise ParserDatasetError("real annotation observation nodes must be a list")
    if any(not isinstance(node, Mapping) or node.get("label_status") == "unlabeled" for node in nodes):
        raise ParserDatasetError("real annotation still contains unlabeled nodes")
    if annotation.get("gold_rows_reviewed") is not True:
        raise ParserDatasetError("real annotation image gold review is not complete")
    document["relations"] = build_relation_labels(nodes)
    document["annotation_status"] = "complete"
    return normalize_parser_documents([document])[0]


__all__ = [
    "ParserDatasetError",
    "RealSourceDataset",
    "annotation_immutable_sha256",
    "finalize_real_annotation",
    "load_real_source_manifest",
    "prepare_real_annotation",
    "REAL_PARSER_LOCK_FILE",
]
