from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Mapping, Sequence

from .observation_profile import runtime_observation_profile
from .training_alignment import align_observation_nodes, build_relation_labels, normalize_semantic_role
from .training_dataset import ParserDatasetError, write_parser_dataset


_SYNTHETIC_OBSERVATION_REVISION = 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_truth_samples(path: str | Path) -> tuple[Path, list[dict[str, Any]]]:
    source = Path(path).resolve()
    if not source.is_file():
        raise ParserDatasetError(f"parser truth samples do not exist: {source}")
    samples: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            sample = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ParserDatasetError(f"parser truth line {line_number} is invalid JSON") from exc
        if not isinstance(sample, dict):
            raise ParserDatasetError(f"parser truth line {line_number} must be an object")
        required = {
            "document_id", "split", "image_sha256", "width", "height", "layout_family",
            "scenario_tags", "risk_tags", "nodes", "positive_edges", "expected_rows",
        }
        missing = sorted(required - set(sample))
        if missing:
            raise ParserDatasetError(f"parser truth line {line_number} is missing fields: {', '.join(missing)}")
        document_id = str(sample.get("document_id") or "")
        if not document_id or document_id in ids:
            raise ParserDatasetError("parser truth document_id values must be non-empty and unique")
        ids.add(document_id)
        if sample.get("split") not in {"train", "val", "test"}:
            raise ParserDatasetError(f"{document_id}.split is invalid")
        if not isinstance(sample.get("nodes"), list):
            raise ParserDatasetError(f"{document_id}.nodes must be a list")
        samples.append(sample)
    if not samples:
        raise ParserDatasetError("parser truth samples are empty")
    return source, samples


def _truth_regions(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for raw in sample["nodes"]:
        if not isinstance(raw, Mapping):
            raise ParserDatasetError(f"{sample['document_id']}.nodes contains non-object")
        regions.append(
            {
                "region_id": str(raw.get("node_id") or ""),
                "semantic_role": str(raw.get("semantic_role") or "other"),
                "association_group": str(raw.get("association_group") or "") or None,
                "polygon": raw.get("polygon"),
                "natural_text_polygon": raw.get("natural_text_polygon") or raw.get("polygon"),
            }
        )
    return regions


def _gold_rows(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    node_by_id = {str(node.get("node_id")): node for node in sample["nodes"] if isinstance(node, Mapping)}
    rows: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(sample["expected_rows"]):
        if not isinstance(raw, Mapping):
            raise ParserDatasetError(f"{sample['document_id']}.expected_rows[{index}] must be an object")
        evidence = raw.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ParserDatasetError(f"{sample['document_id']}.expected_rows[{index}] lacks evidence")
        product_evidence = evidence.get("product_query")
        if not isinstance(product_evidence, list) or not product_evidence:
            raise ParserDatasetError(f"{sample['document_id']}.expected_rows[{index}] lacks product evidence")
        group = None
        for node_id in product_evidence:
            node = node_by_id.get(str(node_id))
            if node is None:
                continue
            candidate = str(node.get("association_group") or "")
            if candidate and candidate != "document":
                group = candidate
                break
        if not group:
            group = str(raw.get("row_id") or f"row-{index + 1}")
        gold_id = group
        suffix = 2
        while gold_id in used_ids:
            gold_id = f"{group}-{suffix}"
            suffix += 1
        used_ids.add(gold_id)
        rows.append(
            {
                "gold_row_id": gold_id,
                "product_query": str(raw.get("product_query") or ""),
                "draft": dict(raw.get("draft") or {}),
            }
        )
    return rows


def _oracle_nodes(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for raw in sample["nodes"]:
        role = normalize_semantic_role(raw.get("semantic_role"))
        group = str(raw.get("association_group") or "") or None
        if role == "other":
            group = None
        nodes.append(
            {
                "node_id": str(raw["node_id"]),
                "text": str(raw.get("text") or ""),
                "confidence": float(raw.get("confidence", 1.0)),
                "polygon": raw.get("natural_text_polygon") or raw["polygon"],
                "target_region_ids": [str(raw["node_id"])],
                "label_status": "labeled",
                "semantic_role": role,
                "association_group": group,
            }
        )
    return nodes


def _jitter_polygon(polygon: object, rng: random.Random, width: int, height: int) -> list[list[float]]:
    if not isinstance(polygon, list) or len(polygon) != 4:
        raise ParserDatasetError("truth node polygon must contain four points")
    max_dx = max(1.0, width * 0.0025)
    max_dy = max(1.0, height * 0.0025)
    dx = rng.uniform(-max_dx, max_dx)
    dy = rng.uniform(-max_dy, max_dy)
    return [
        [max(0.0, min(float(point[0]) + dx, float(width))), max(0.0, min(float(point[1]) + dy, float(height)))]
        for point in polygon
    ]


def _bbox(polygon: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _rect(x1: float, y1: float, x2: float, y2: float) -> list[list[float]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _corrupt_text(text: str, rng: random.Random) -> str:
    if len(text) < 2 or rng.random() >= 0.10:
        return text
    index = rng.randrange(len(text))
    if rng.random() < 0.5:
        return text[:index] + text[index + 1 :]
    substitutions = {"1": "I", "0": "O", "정": "점", "회": "외", "일": "|"}
    replacement = substitutions.get(text[index], text[index])
    return text[:index] + replacement + text[index + 1 :]


def _synthetic_observed_regions(sample: Mapping[str, Any], seed: int) -> list[dict[str, Any]]:
    document_id = str(sample["document_id"])
    digest = hashlib.sha256(f"{seed}:{document_id}:{_SYNTHETIC_OBSERVATION_REVISION}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    width = int(sample["width"])
    height = int(sample["height"])
    observed: list[dict[str, Any]] = []
    for raw in sample["nodes"]:
        role = normalize_semantic_role(raw.get("semantic_role"))
        drop_rate = 0.06 if role in {"product", "dose", "frequency", "duration"} else 0.035
        if rng.random() < drop_rate:
            continue
        polygon = _jitter_polygon(raw.get("natural_text_polygon") or raw["polygon"], rng, width, height)
        text = _corrupt_text(str(raw.get("text") or ""), rng)
        confidence = round(rng.uniform(0.72, 0.995), 6)
        x1, y1, x2, y2 = _bbox(polygon)
        if len(text) >= 3 and x2 - x1 >= 36 and rng.random() < 0.10:
            split_at = max(1, min(len(text) - 1, len(text) // 2))
            split_x = x1 + (x2 - x1) * (split_at / len(text))
            observed.append({"text": text[:split_at], "recognition_score": confidence, "polygon": _rect(x1, y1, split_x, y2)})
            observed.append({"text": text[split_at:], "recognition_score": max(0.0, confidence - 0.02), "polygon": _rect(split_x, y1, x2, y2)})
        else:
            observed.append({"text": text, "recognition_score": confidence, "polygon": polygon})

    # Merge at most one close visual-row pair. The alignment layer decides whether
    # the merged observation is still labelable or must be masked as ambiguous.
    spatial = sorted(range(len(observed)), key=lambda index: (_bbox(observed[index]["polygon"])[1], _bbox(observed[index]["polygon"])[0]))
    for left_index, right_index in zip(spatial, spatial[1:]):
        if rng.random() >= 0.08:
            continue
        left = observed[left_index]
        right = observed[right_index]
        lx1, ly1, lx2, ly2 = _bbox(left["polygon"])
        rx1, ry1, rx2, ry2 = _bbox(right["polygon"])
        vertical_overlap = max(0.0, min(ly2, ry2) - max(ly1, ry1))
        if vertical_overlap < 0.45 * max(1.0, min(ly2 - ly1, ry2 - ry1)):
            continue
        gap = max(0.0, rx1 - lx2, lx1 - rx2)
        if gap > max(28.0, 1.5 * max(ly2 - ly1, ry2 - ry1)):
            continue
        merged = {
            "text": f"{left['text']}{right['text']}",
            "recognition_score": min(float(left["recognition_score"]), float(right["recognition_score"])),
            "polygon": _rect(min(lx1, rx1), min(ly1, ry1), max(lx2, rx2), max(ly2, ry2)),
        }
        first, second = sorted((left_index, right_index), reverse=True)
        observed.pop(first)
        observed.pop(second)
        observed.append(merged)
        break

    if rng.random() < 0.30:
        noise_x = rng.uniform(width * 0.05, width * 0.75)
        noise_y = rng.uniform(height * 0.70, height * 0.94)
        observed.append(
            {
                "text": rng.choice(["주의", "TEL", "보관", "문의"]),
                "recognition_score": round(rng.uniform(0.45, 0.88), 6),
                "polygon": _rect(noise_x, noise_y, min(width, noise_x + 80), min(height, noise_y + 28)),
            }
        )

    rng.shuffle(observed)
    for index, region in enumerate(observed, start=1):
        region["index"] = index
    return observed


def _base_document(
    sample: Mapping[str, Any],
    *,
    observation_kind: str,
    observation_profile: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized_nodes = [dict(node) for node in nodes]
    return {
        "document_id": str(sample["document_id"]),
        "split": str(sample["split"]),
        "source_kind": "synthetic",
        "image_sha256": str(sample["image_sha256"]),
        "width": int(sample["width"]),
        "height": int(sample["height"]),
        "layout_family": str(sample["layout_family"]),
        "scenario_tags": list(sample["scenario_tags"]),
        "risk_tags": list(sample["risk_tags"]),
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "observation": {"kind": observation_kind, "profile": dict(observation_profile), "nodes": normalized_nodes},
        "relations": build_relation_labels(normalized_nodes),
        "gold_rows": _gold_rows(sample),
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }



def build_synthetic_dataset(
    *,
    truth_samples_path: str | Path,
    output_dir: str | Path,
    dataset_id: str,
    observation_kind: str,
    split: str | None = None,
    seed: int = 112,
) -> Path:
    if observation_kind not in {"oracle", "synthetic_ocr"}:
        raise ParserDatasetError("synthetic observation_kind must be oracle or synthetic_ocr")
    source, samples = _load_truth_samples(truth_samples_path)
    selected = [sample for sample in samples if split is None or sample["split"] == split]
    if not selected:
        raise ParserDatasetError("no parser truth samples matched the requested split")
    source_sha = _sha256_file(source)
    documents: list[dict[str, Any]] = []
    for sample in selected:
        if observation_kind == "oracle":
            nodes = _oracle_nodes(sample)
            profile = {"producer": "unified_truth", "truth_samples_sha256": source_sha}
        else:
            observed = _synthetic_observed_regions(sample, seed)
            nodes = align_observation_nodes(_truth_regions(sample), observed)
            profile = {
                "producer": "deterministic_synthetic_ocr",
                "revision": _SYNTHETIC_OBSERVATION_REVISION,
                "seed": seed,
                "truth_samples_sha256": source_sha,
            }
        documents.append(
            _base_document(
                sample,
                observation_kind=observation_kind,
                observation_profile=profile,
                nodes=nodes,
            )
        )
    return write_parser_dataset(
        output_dir,
        dataset_id=dataset_id,
        documents=documents,
        metadata={
            "builder": "parser_training_builder_v1",
            "truth_samples_sha256": source_sha,
            "observation_kind": observation_kind,
            "split": split or "all",
            "seed": seed,
        },
    )


def build_runtime_dataset(
    *,
    truth_samples_path: str | Path,
    results_root: str | Path,
    output_dir: str | Path,
    dataset_id: str,
    split: str | None = None,
) -> Path:
    source, samples = _load_truth_samples(truth_samples_path)
    selected = [sample for sample in samples if split is None or sample["split"] == split]
    if not selected:
        raise ParserDatasetError("no parser truth samples matched the requested split")
    root = Path(results_root).resolve()
    documents: list[dict[str, Any]] = []
    for sample in selected:
        document_id = str(sample["document_id"])
        result_path = root / document_id / "result.json"
        if not result_path.is_file():
            raise ParserDatasetError(f"missing runtime OCR result: {result_path}")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ParserDatasetError(f"runtime OCR result is invalid JSON: {result_path}") from exc
        if not isinstance(result, Mapping) or result.get("status") != "ok":
            raise ParserDatasetError(f"runtime OCR result is not successful: {result_path}")
        image = result.get("image")
        if not isinstance(image, Mapping) or str(image.get("sha256") or "") != str(sample["image_sha256"]):
            raise ParserDatasetError(f"runtime OCR result image hash does not match truth: {document_id}")
        observed_regions = result.get("regions")
        if not isinstance(observed_regions, list):
            raise ParserDatasetError(f"runtime OCR result regions are missing: {document_id}")
        nodes = align_observation_nodes(_truth_regions(sample), observed_regions)
        documents.append(
            _base_document(
                sample,
                observation_kind="runtime_ocr",
                observation_profile=runtime_observation_profile(
                    result.get("profile"),
                    expected_image_sha256=str(sample["image_sha256"]),
                ),
                nodes=nodes,
            )
        )
    return write_parser_dataset(
        output_dir,
        dataset_id=dataset_id,
        documents=documents,
        metadata={
            "builder": "parser_runtime_builder_v1",
            "truth_samples_sha256": _sha256_file(source),
            "observation_kind": "runtime_ocr",
            "split": split or "all",
        },
    )


__all__ = ["build_runtime_dataset", "build_synthetic_dataset"]
