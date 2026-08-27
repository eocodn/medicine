from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_storage import atomic_write
from .parser_v5_dataset import load_parser_v5_dataset
from .parser_v5_observation import ObservationProfile
from .region_alignment import match_region_candidates, match_regions_one_to_one, observed_region_id


CALIBRATION_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = {
    "schema_version",
    "dataset_id",
    "dataset_samples_sha256",
    "producer_fingerprint",
    "runtime_records_sha256",
    "document_count",
    "summary",
    "recommended_observation_profile",
    "calibration_fingerprint",
}
_RUNTIME_RECORD_FIELDS = {"document_id", "source_split", "producer_fingerprint", "nodes"}
_RUNTIME_NODE_FIELDS = {
    "index",
    "text",
    "detector_confidence",
    "recognizer_confidence",
    "polygon",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compact(text: object) -> str:
    return "".join(str(text or "").split()).lower()


def _quantile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _center(polygon: Sequence[Sequence[float]]) -> tuple[float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _runtime_node(value: object, *, document_id: str, fallback_index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_NODE_FIELDS:
        raise ValueError(f"Parser v5 calibration {document_id} runtime node fields are invalid")
    index = value.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        raise ValueError(f"Parser v5 calibration {document_id} runtime node index must be positive")
    polygon = value.get("polygon")
    if not isinstance(polygon, list) or len(polygon) != 4:
        raise ValueError(f"Parser v5 calibration {document_id} runtime node polygon is invalid")
    normalized_polygon: list[list[float]] = []
    for point in polygon:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"Parser v5 calibration {document_id} runtime node polygon point is invalid")
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError(f"Parser v5 calibration {document_id} runtime node polygon coordinates must be numeric")
        if not math.isfinite(float(x)) or not math.isfinite(float(y)):
            raise ValueError(f"Parser v5 calibration {document_id} runtime node polygon coordinates must be finite")
        normalized_polygon.append([float(x), float(y)])
    result = {
        "index": index,
        "text": str(value.get("text") or ""),
        "polygon": normalized_polygon,
    }
    for name in ("detector_confidence", "recognizer_confidence"):
        score = value.get(name)
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            raise ValueError(f"Parser v5 calibration {document_id} {name} must be in [0, 1]")
        result[name] = float(score)
    return result


def _record(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_RECORD_FIELDS:
        raise ValueError("Parser v5 calibration runtime record fields are invalid")
    document_id = str(value.get("document_id") or "").strip()
    if not document_id:
        raise ValueError("Parser v5 calibration runtime document_id is required")
    if value.get("source_split") != "train":
        raise ValueError("Parser v5 calibration is train-only")
    producer = str(value.get("producer_fingerprint") or "")
    if not _SHA256_RE.fullmatch(producer):
        raise ValueError("Parser v5 calibration producer_fingerprint must be lowercase SHA-256")
    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError(f"Parser v5 calibration {document_id} nodes must be a list")
    nodes = [_runtime_node(node, document_id=document_id, fallback_index=index) for index, node in enumerate(raw_nodes, 1)]
    indices = [node["index"] for node in nodes]
    if len(indices) != len(set(indices)):
        raise ValueError(f"Parser v5 calibration {document_id} runtime node indices must be unique")
    return {
        "document_id": document_id,
        "source_split": "train",
        "producer_fingerprint": producer,
        "nodes": nodes,
    }


def _truth_regions(truth: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "region_id": str(span["span_id"]),
            "text": str(span["text"]),
            "reading_order": int(span["reading_order"]),
            "polygon": span["polygon"],
            "natural_text_polygon": span["polygon"],
        }
        for span in truth["spans"]
    ]


def _profile_summary(dataset, records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    truth_by_id = {str(sample["truth"]["document_id"]): sample["truth"] for sample in dataset.samples}
    truth_total = truth_dropped = truth_split = truth_duplicate = 0
    observed_total = observed_merged = observed_false_positive = observed_text_error = matched_observed = 0
    detector_confidences: list[float] = []
    recognizer_confidences: list[float] = []
    geometry_shifts: list[float] = []
    false_positive_counts: list[int] = []
    order_pairs = order_inversions = 0

    for record in records:
        document_id = str(record["document_id"])
        truth = truth_by_id[document_id]
        width = float(truth["width"])
        height = float(truth["height"])
        regions = _truth_regions(truth)
        hits = {str(region["region_id"]): [] for region in regions}
        primary = match_regions_one_to_one(regions, record["nodes"], minimum_truth_coverage=0.80)
        primary_truth_ids = {str(region["region_id"]) for region in primary.values()}
        matched_orders: list[int] = []
        false_positives = 0
        for fallback_index, node in enumerate(record["nodes"], start=1):
            observed_total += 1
            detector_confidences.append(float(node["detector_confidence"]))
            recognizer_confidences.append(float(node["recognizer_confidence"]))
            matches = match_region_candidates(regions, node)
            primary_region = primary.get(observed_region_id(node, fallback_index))
            if primary_region is None and not matches:
                observed_false_positive += 1
                false_positives += 1
                continue
            matched_observed += 1
            effective: list[tuple[Mapping[str, Any], tuple[float, float, float]]] = []
            if primary_region is not None:
                primary_id = str(primary_region["region_id"])
                primary_match = next((item for item in matches if str(item[0]["region_id"]) == primary_id), None)
                if primary_match is None:
                    primary_match = (primary_region, (1.0, 1.0, 1.0))
                effective.append(primary_match)
                for candidate in matches:
                    candidate_id = str(candidate[0]["region_id"])
                    if candidate_id != primary_id and candidate_id not in primary_truth_ids and float(candidate[1][0]) >= 0.80:
                        effective.append(candidate)
            else:
                effective.append(matches[0])
            if len(effective) > 1:
                observed_merged += 1
            ordered_matches = sorted(effective, key=lambda item: int(item[0]["reading_order"]))
            expected_text = _compact("".join(str(item[0]["text"]) for item in ordered_matches))
            if _compact(node["text"]) != expected_text:
                observed_text_error += 1
            best_region, best_overlap = effective[0]
            matched_orders.append(int(best_region["reading_order"]))
            if len(effective) == 1 and primary_region is not None:
                tcx, tcy = _center(best_region["polygon"])
                ocx, ocy = _center(node["polygon"])
                geometry_shifts.append(math.hypot((ocx - tcx) / width, (ocy - tcy) / height))
            for region, overlap in effective:
                hits[str(region["region_id"])].append((node, overlap))
        false_positive_counts.append(false_positives)
        for left, right in zip(matched_orders, matched_orders[1:]):
            order_pairs += 1
            order_inversions += int(right < left)
        truth_total += len(regions)
        for matched in hits.values():
            if not matched:
                truth_dropped += 1
            elif len(matched) > 1:
                if sum(float(overlap[0]) >= 0.80 for _, overlap in matched) >= 2:
                    truth_duplicate += 1
                else:
                    truth_split += 1

    summary = {
        "truth_span_count": truth_total,
        "observed_node_count": observed_total,
        "drop_rate": truth_dropped / max(truth_total, 1),
        "split_rate": truth_split / max(truth_total, 1),
        "duplicate_rate": truth_duplicate / max(truth_total, 1),
        "merge_rate": observed_merged / max(observed_total, 1),
        "false_positive_rate": observed_false_positive / max(observed_total, 1),
        "recognition_error_rate": observed_text_error / max(matched_observed, 1),
        "geometry_shift_mean": sum(geometry_shifts) / max(len(geometry_shifts), 1),
        "reading_order_inversion_rate": order_inversions / max(order_pairs, 1),
        "false_positive_count_min": min(false_positive_counts, default=0),
        "false_positive_count_max": max(false_positive_counts, default=0),
        "detector_confidence_p10": _quantile(detector_confidences, 0.10),
        "detector_confidence_p50": _quantile(detector_confidences, 0.50),
        "detector_confidence_p90": _quantile(detector_confidences, 0.90),
        "recognizer_confidence_p10": _quantile(recognizer_confidences, 0.10),
        "recognizer_confidence_p50": _quantile(recognizer_confidences, 0.50),
        "recognizer_confidence_p90": _quantile(recognizer_confidences, 0.90),
    }
    recommended = asdict(ObservationProfile(
        text_corruption_rate=min(1.0, float(summary["recognition_error_rate"])),
        drop_rate=min(1.0, float(summary["drop_rate"])),
        duplicate_rate=min(1.0, float(summary["duplicate_rate"])),
        split_rate=min(1.0, float(summary["split_rate"])),
        merge_rate=min(1.0, float(summary["merge_rate"])),
        geometry_jitter=min(0.25, float(summary["geometry_shift_mean"])),
        false_positive_count=(
            int(summary["false_positive_count_min"]),
            int(summary["false_positive_count_max"]),
        ),
        reading_order_shuffle_rate=min(1.0, float(summary["reading_order_inversion_rate"])),
    ))
    return summary, recommended


def build_parser_v5_calibration(
    *,
    dataset_manifest: str | Path,
    runtime_records: Sequence[Mapping[str, Any]],
    output_path: str | Path,
) -> Path:
    dataset = load_parser_v5_dataset(dataset_manifest)
    if not runtime_records:
        raise ValueError("Parser v5 calibration requires runtime records")
    records = [_record(record) for record in runtime_records]
    ids = [record["document_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Parser v5 calibration document_ids must be unique")
    truth_ids = {str(sample["truth"]["document_id"]) for sample in dataset.samples}
    if set(ids) != truth_ids:
        raise ValueError("Parser v5 calibration runtime document set must exactly match the train dataset")
    producers = {str(record["producer_fingerprint"]) for record in records}
    if len(producers) != 1:
        raise ValueError("Parser v5 calibration runtime records must use one frozen producer")
    records = sorted(records, key=lambda record: str(record["document_id"]))
    summary, recommended = _profile_summary(dataset, records)
    payload = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_samples_sha256": dataset.samples_sha256,
        "producer_fingerprint": next(iter(producers)),
        "runtime_records_sha256": _sha256(_canonical_json(records)),
        "document_count": len(records),
        "summary": summary,
        "recommended_observation_profile": recommended,
    }
    artifact = {**payload, "calibration_fingerprint": _sha256(_canonical_json(payload))}
    destination = Path(output_path).resolve()
    atomic_write(destination, _canonical_json(artifact) + b"\n")
    return destination


def load_parser_v5_calibration(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5 calibration artifact: {source}") from exc
    if not isinstance(value, Mapping) or set(value) != _ARTIFACT_FIELDS:
        raise ValueError("Parser v5 calibration artifact fields are invalid")
    if value.get("schema_version") != CALIBRATION_SCHEMA_VERSION:
        raise ValueError("Parser v5 calibration schema_version must be 1")
    for field in ("dataset_samples_sha256", "producer_fingerprint", "runtime_records_sha256", "calibration_fingerprint"):
        if not _SHA256_RE.fullmatch(str(value.get(field) or "")):
            raise ValueError(f"Parser v5 calibration {field} must be lowercase SHA-256")
    payload = {key: value[key] for key in value if key != "calibration_fingerprint"}
    if _sha256(_canonical_json(payload)) != value["calibration_fingerprint"]:
        raise ValueError("Parser v5 calibration fingerprint mismatch")
    recommended = value.get("recommended_observation_profile")
    if not isinstance(recommended, Mapping):
        raise ValueError("Parser v5 calibration recommended profile is invalid")
    normalized = dict(recommended)
    raw_fp = normalized.get("false_positive_count")
    if isinstance(raw_fp, list):
        normalized["false_positive_count"] = tuple(raw_fp)
    ObservationProfile(**normalized)
    return dict(value)


__all__ = [
    "build_parser_v5_calibration",
    "load_parser_v5_calibration",
]