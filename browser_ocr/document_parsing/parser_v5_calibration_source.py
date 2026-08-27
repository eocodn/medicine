from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .observation_profile import runtime_observation_producer
from .training_dataset import ParserDataset, load_parser_dataset


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROTATIONS = {0, 90, 180, 270}


@dataclass(frozen=True)
class ParserV5CalibrationSource:
    source_identity: Mapping[str, Any]
    source_fingerprint: str
    producer_fingerprint: str
    truth_samples_sha256: str
    document_count: int
    documents: tuple[dict[str, Any], ...]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5 calibration {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Parser v5 calibration {label} must be an object")
    return value


def _manifest_identity(dataset: ParserDataset) -> dict[str, Any]:
    manifest = _json_object(dataset.manifest_path, label="dataset manifest")
    samples_sha256 = str(manifest.get("samples_sha256") or "")
    if not _SHA256_RE.fullmatch(samples_sha256):
        raise ValueError("Parser v5 calibration dataset samples_sha256 is invalid")
    return {
        "dataset_id": dataset.dataset_id,
        "manifest_sha256": _sha256_file(dataset.manifest_path),
        "samples_sha256": samples_sha256,
        "dataset_fingerprint": dataset.fingerprint,
    }


def _require_dataset_role(dataset: ParserDataset, *, observation_kind: str) -> str:
    metadata = dataset.metadata
    if metadata.get("split") != "train":
        raise ValueError("Parser v5 calibration source is train-only")
    if metadata.get("observation_kind") != observation_kind:
        raise ValueError(f"Parser v5 calibration source requires {observation_kind} observations")
    truth_sha = str(metadata.get("truth_samples_sha256") or "")
    if not _SHA256_RE.fullmatch(truth_sha):
        raise ValueError("Parser v5 calibration truth_samples_sha256 is invalid")
    return truth_sha


def _polygon(value: object, *, label: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"Parser v5 calibration {label} polygon is invalid")
    result: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"Parser v5 calibration {label} polygon point is invalid")
        x, y = point
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise ValueError(f"Parser v5 calibration {label} polygon coordinates are invalid")
        result.append([float(x), float(y)])
    return result


def _score(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"Parser v5 calibration {label} must be in [0, 1]")
    return float(value)


def _oracle_regions(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    observation = document.get("observation")
    raw_nodes = observation.get("nodes") if isinstance(observation, Mapping) else None
    if not isinstance(raw_nodes, list):
        raise ValueError("Parser v5 calibration oracle nodes are missing")
    regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, node in enumerate(raw_nodes):
        if not isinstance(node, Mapping) or node.get("label_status") != "labeled":
            raise ValueError("Parser v5 calibration oracle nodes must be labeled")
        targets = node.get("target_region_ids")
        if not isinstance(targets, list) or len(targets) != 1:
            raise ValueError("Parser v5 calibration oracle node must map to exactly one truth region")
        region_id = str(targets[0] or "")
        if not region_id or region_id in seen:
            raise ValueError("Parser v5 calibration oracle truth region ids must be unique")
        seen.add(region_id)
        regions.append({
            "region_id": region_id,
            "text": str(node.get("text") or ""),
            "polygon": _polygon(node.get("polygon"), label=f"oracle {region_id}"),
            "source_order": order,
        })
    if not regions:
        raise ValueError("Parser v5 calibration oracle document has no truth regions")
    return tuple(regions)


def _runtime_nodes(document: Mapping[str, Any], raw_result: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    observation = document.get("observation")
    nodes = observation.get("nodes") if isinstance(observation, Mapping) else None
    regions = raw_result.get("regions")
    if not isinstance(nodes, list) or not isinstance(regions, list) or len(nodes) != len(regions):
        raise ValueError("Parser v5 calibration runtime dataset and raw runtime node counts disagree")
    by_id: dict[str, Mapping[str, Any]] = {}
    for raw_region in regions:
        if not isinstance(raw_region, Mapping):
            raise ValueError("Parser v5 calibration raw runtime region must be an object")
        index = raw_region.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
            raise ValueError("Parser v5 calibration raw runtime region index is invalid")
        region_id = f"region-{index:04d}"
        if region_id in by_id:
            raise ValueError("Parser v5 calibration raw runtime region indices must be unique")
        by_id[region_id] = raw_region

    result: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise ValueError("Parser v5 calibration runtime node must be an object")
        node_id = str(node.get("node_id") or "")
        raw_region = by_id.get(node_id)
        if raw_region is None:
            raise ValueError("Parser v5 calibration runtime dataset node is missing from raw runtime result")
        node_polygon = _polygon(node.get("polygon"), label=f"runtime dataset {node_id}")
        raw_polygon = _polygon(raw_region.get("polygon"), label=f"raw runtime {node_id}")
        if str(node.get("text") or "") != str(raw_region.get("text") or "") or node_polygon != raw_polygon:
            raise ValueError("Parser v5 calibration runtime dataset disagrees with raw runtime result")
        recognition = _score(raw_region.get("recognition_score"), label=f"{node_id} recognizer confidence")
        if abs(float(node.get("confidence") or 0.0) - recognition) > 1e-9:
            raise ValueError("Parser v5 calibration runtime dataset confidence disagrees with raw runtime result")
        targets = node.get("target_region_ids")
        if not isinstance(targets, list) or any(not str(target or "") for target in targets):
            raise ValueError("Parser v5 calibration runtime target_region_ids are invalid")
        result.append({
            "index": int(raw_region["index"]),
            "text": str(raw_region.get("text") or ""),
            "polygon": raw_polygon,
            "detector_confidence": _score(
                raw_region.get("detection_score"), label=f"{node_id} detector confidence"
            ),
            "recognizer_confidence": recognition,
            "target_region_ids": [str(target) for target in targets],
        })
    return tuple(result)


def _rotation(raw_result: Mapping[str, Any]) -> int:
    stages = raw_result.get("stages")
    orientation = stages.get("orientation") if isinstance(stages, Mapping) else None
    rotation = orientation.get("applied_rotation_degrees") if isinstance(orientation, Mapping) else None
    if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in _ROTATIONS:
        raise ValueError("Parser v5 calibration raw runtime orientation rotation is invalid")
    return rotation


def _raw_result(
    *,
    batch_root: Path,
    document_id: str,
    expected_sha256: str,
    runtime_document: Mapping[str, Any],
    producer: Mapping[str, Any],
) -> dict[str, Any]:
    if not _SHA256_RE.fullmatch(expected_sha256):
        raise ValueError("Parser v5 calibration raw runtime result SHA-256 is invalid")
    path = batch_root / "runtime" / document_id / "result.json"
    if not path.is_file() or _sha256_file(path) != expected_sha256:
        raise ValueError(f"Parser v5 calibration raw runtime result SHA-256 mismatch for {document_id}")
    result = _json_object(path, label=f"raw runtime result {document_id}")
    if result.get("status") != "ok":
        raise ValueError(f"Parser v5 calibration raw runtime result is not completed for {document_id}")
    image = result.get("image")
    if not isinstance(image, Mapping):
        raise ValueError(f"Parser v5 calibration raw runtime image identity is invalid for {document_id}")
    if image.get("sha256") != runtime_document.get("image_sha256"):
        raise ValueError(f"Parser v5 calibration raw runtime image SHA-256 mismatch for {document_id}")
    if image.get("width") != runtime_document.get("width") or image.get("height") != runtime_document.get("height"):
        raise ValueError(f"Parser v5 calibration raw runtime image dimensions mismatch for {document_id}")
    raw_producer = runtime_observation_producer(
        result.get("profile"), expected_image_sha256=str(runtime_document.get("image_sha256") or "")
    )
    if raw_producer != dict(producer):
        raise ValueError(f"Parser v5 calibration raw runtime producer mismatch for {document_id}")
    return result


def load_parser_v5_calibration_source(
    *,
    oracle_manifest: str | Path,
    runtime_manifest: str | Path,
    runtime_batch_result: str | Path,
) -> ParserV5CalibrationSource:
    oracle = load_parser_dataset(oracle_manifest)
    runtime = load_parser_dataset(runtime_manifest)
    oracle_truth = _require_dataset_role(oracle, observation_kind="oracle")
    runtime_truth = _require_dataset_role(runtime, observation_kind="runtime_ocr")
    if oracle_truth != runtime_truth:
        raise ValueError("Parser v5 calibration oracle and runtime truth identity disagree")
    producer = runtime.metadata.get("ocr_producer")
    if not isinstance(producer, Mapping):
        raise ValueError("Parser v5 calibration runtime dataset producer is missing")

    oracle_by_id = {str(document["document_id"]): document for document in oracle.documents}
    runtime_by_id = {str(document["document_id"]): document for document in runtime.documents}
    if set(oracle_by_id) != set(runtime_by_id):
        raise ValueError("Parser v5 calibration oracle/runtime document sets must exactly match")
    if not oracle_by_id:
        raise ValueError("Parser v5 calibration source document set is empty")

    batch_path = Path(runtime_batch_result).resolve()
    batch = _json_object(batch_path, label="runtime batch result")
    if batch.get("status") != "ok":
        raise ValueError("Parser v5 calibration runtime batch must be completed")
    batch_profile = batch.get("profile")
    if not isinstance(batch_profile, Mapping):
        raise ValueError("Parser v5 calibration runtime batch profile is invalid")
    if batch_profile.get("truth_samples_sha256") != oracle_truth:
        raise ValueError("Parser v5 calibration runtime batch truth identity disagrees")
    if batch_profile.get("ocr_producer") != dict(producer):
        raise ValueError("Parser v5 calibration runtime batch producer disagrees")
    runtime_results = batch.get("runtime_results")
    if not isinstance(runtime_results, Mapping):
        raise ValueError("Parser v5 calibration runtime batch result hashes are missing")

    documents: list[dict[str, Any]] = []
    train_result_hashes: list[dict[str, str]] = []
    for document_id in sorted(oracle_by_id):
        oracle_document = oracle_by_id[document_id]
        runtime_document = runtime_by_id[document_id]
        if oracle_document.get("split") != "train" or runtime_document.get("split") != "train":
            raise ValueError("Parser v5 calibration source is train-only")
        for field in ("image_sha256", "source_binding"):
            if oracle_document.get(field) != runtime_document.get(field):
                raise ValueError(f"Parser v5 calibration oracle/runtime {field} identity disagree")
        expected_raw_sha = str(runtime_results.get(document_id) or "")
        raw = _raw_result(
            batch_root=batch_path.parent,
            document_id=document_id,
            expected_sha256=expected_raw_sha,
            runtime_document=runtime_document,
            producer=producer,
        )
        image = raw["image"]
        if image.get("source_width") != oracle_document.get("width") or image.get("source_height") != oracle_document.get("height"):
            raise ValueError(f"Parser v5 calibration oracle/raw source dimensions disagree for {document_id}")
        documents.append({
            "document_id": document_id,
            "source_width": int(oracle_document["width"]),
            "source_height": int(oracle_document["height"]),
            "runtime_width": int(runtime_document["width"]),
            "runtime_height": int(runtime_document["height"]),
            "rotation_degrees": _rotation(raw),
            "truth_regions": list(_oracle_regions(oracle_document)),
            "runtime_nodes": list(_runtime_nodes(runtime_document, raw)),
        })
        train_result_hashes.append({"document_id": document_id, "result_sha256": expected_raw_sha})

    producer_fingerprint = _sha256_bytes(_canonical_json(producer))
    source_identity = {
        "schema_version": 1,
        "oracle_dataset": _manifest_identity(oracle),
        "runtime_dataset": _manifest_identity(runtime),
        "runtime_batch_result_sha256": _sha256_file(batch_path),
        "runtime_train_results_sha256": _sha256_bytes(_canonical_json(train_result_hashes)),
        "truth_samples_sha256": oracle_truth,
        "producer_fingerprint": producer_fingerprint,
        "document_count": len(documents),
    }
    return ParserV5CalibrationSource(
        source_identity=source_identity,
        source_fingerprint=_sha256_bytes(_canonical_json(source_identity)),
        producer_fingerprint=producer_fingerprint,
        truth_samples_sha256=oracle_truth,
        document_count=len(documents),
        documents=tuple(documents),
    )


__all__ = ["ParserV5CalibrationSource", "load_parser_v5_calibration_source"]