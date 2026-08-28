from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_storage import atomic_write
from .parser_v5_calibration_source import ParserV5CalibrationSource, load_parser_v5_calibration_source
from .parser_v5_observation import ObservationProfile


CALIBRATION_SCHEMA_VERSION = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = {
    "schema_version",
    "source_identity",
    "source_fingerprint",
    "producer_fingerprint",
    "document_count",
    "summary",
    "recommended_observation_profile",
    "calibration_fingerprint",
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


def _rotate_point(
    x: float,
    y: float,
    *,
    rotation: int,
    source_width: float,
    source_height: float,
) -> tuple[float, float]:
    if rotation == 0:
        return x, y
    if rotation == 90:
        return source_height - y, x
    if rotation == 180:
        return source_width - x, source_height - y
    if rotation == 270:
        return y, source_width - x
    raise ValueError("Parser v5 calibration rotation is unsupported")


def _rotate_polygon(
    polygon: Sequence[Sequence[float]],
    *,
    rotation: int,
    source_width: float,
    source_height: float,
) -> list[list[float]]:
    return [
        list(
            _rotate_point(
                float(point[0]),
                float(point[1]),
                rotation=rotation,
                source_width=source_width,
                source_height=source_height,
            )
        )
        for point in polygon
    ]


def _profile_summary(source: ParserV5CalibrationSource) -> tuple[dict[str, Any], dict[str, Any]]:
    truth_total = truth_dropped = truth_split = truth_duplicate = 0
    observed_total = observed_merged = observed_false_positive = observed_text_error = matched_observed = 0
    detector_confidences: list[float] = []
    recognizer_confidences: list[float] = []
    geometry_shifts: list[float] = []
    false_positive_counts: list[int] = []
    order_pairs = order_inversions = 0

    for document in source.documents:
        truth_regions = document["truth_regions"]
        runtime_nodes = document["runtime_nodes"]
        truth_by_id = {str(region["region_id"]): region for region in truth_regions}
        hits: dict[str, list[Mapping[str, Any]]] = {region_id: [] for region_id in truth_by_id}
        false_positives = 0
        observed_orders: list[int] = []
        truth_total += len(truth_regions)

        for node in runtime_nodes:
            observed_total += 1
            detector_confidences.append(float(node["detector_confidence"]))
            recognizer_confidences.append(float(node["recognizer_confidence"]))
            target_ids = [str(value) for value in node["target_region_ids"]]
            unknown = [target_id for target_id in target_ids if target_id not in truth_by_id]
            if unknown:
                raise ValueError(
                    f"Parser v5 calibration runtime target ids are absent from oracle truth: {', '.join(unknown)}"
                )
            if not target_ids:
                observed_false_positive += 1
                false_positives += 1
                continue
            matched_observed += 1
            if len(target_ids) > 1:
                observed_merged += 1
            ordered_regions = sorted(
                (truth_by_id[target_id] for target_id in target_ids),
                key=lambda region: int(region["source_order"]),
            )
            expected_text = _compact("".join(str(region["text"]) for region in ordered_regions))
            if _compact(node["text"]) != expected_text:
                observed_text_error += 1
            observed_orders.append(min(int(region["source_order"]) for region in ordered_regions))
            for target_id in target_ids:
                hits[target_id].append(node)

            if len(target_ids) == 1:
                truth_region = truth_by_id[target_ids[0]]
                transformed = _rotate_polygon(
                    truth_region["polygon"],
                    rotation=int(document["rotation_degrees"]),
                    source_width=float(document["source_width"]),
                    source_height=float(document["source_height"]),
                )
                tcx, tcy = _center(transformed)
                ocx, ocy = _center(node["polygon"])
                geometry_shifts.append(
                    math.hypot(
                        (ocx - tcx) / max(float(document["runtime_width"]), 1.0),
                        (ocy - tcy) / max(float(document["runtime_height"]), 1.0),
                    )
                )

        false_positive_counts.append(false_positives)
        for left, right in zip(observed_orders, observed_orders[1:]):
            order_pairs += 1
            order_inversions += int(right < left)

        for region_id, matched in hits.items():
            if not matched:
                truth_dropped += 1
                continue
            if len(matched) <= 1:
                continue
            truth_text = _compact(truth_by_id[region_id]["text"])
            full_copies = sum(_compact(node["text"]) == truth_text for node in matched)
            if full_copies >= 2:
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
    recommended = asdict(
        ObservationProfile(
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
        )
    )
    return summary, recommended


def build_parser_v5_calibration(
    *,
    oracle_manifest: str | Path,
    runtime_manifest: str | Path,
    runtime_batch_result: str | Path,
    output_path: str | Path,
) -> Path:
    source = load_parser_v5_calibration_source(
        oracle_manifest=oracle_manifest,
        runtime_manifest=runtime_manifest,
        runtime_batch_result=runtime_batch_result,
    )
    summary, recommended = _profile_summary(source)
    payload = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "source_identity": dict(source.source_identity),
        "source_fingerprint": source.source_fingerprint,
        "producer_fingerprint": source.producer_fingerprint,
        "document_count": source.document_count,
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
        raise ValueError("Parser v5 calibration schema_version must be 2")
    for field in ("source_fingerprint", "producer_fingerprint", "calibration_fingerprint"):
        if not _SHA256_RE.fullmatch(str(value.get(field) or "")):
            raise ValueError(f"Parser v5 calibration {field} must be lowercase SHA-256")
    source_identity = value.get("source_identity")
    if not isinstance(source_identity, Mapping):
        raise ValueError("Parser v5 calibration source_identity is invalid")
    if _sha256(_canonical_json(source_identity)) != value["source_fingerprint"]:
        raise ValueError("Parser v5 calibration source fingerprint mismatch")
    if source_identity.get("producer_fingerprint") != value["producer_fingerprint"]:
        raise ValueError("Parser v5 calibration producer fingerprint disagrees with source")
    count = value.get("document_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("Parser v5 calibration document_count must be positive")
    if source_identity.get("document_count") != count:
        raise ValueError("Parser v5 calibration document_count disagrees with source")
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


__all__ = ["build_parser_v5_calibration", "load_parser_v5_calibration"]