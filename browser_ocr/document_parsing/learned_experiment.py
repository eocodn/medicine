from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from browser_ocr.finetune.full_document import recognition_quality
from browser_ocr.finetune.full_document_evaluation import _match_regions, evaluate_sample

from .learned_layout import (
    ROLE_LABELS,
    LayoutNode,
    LabeledDocument,
    SemanticExample,
    edge_score,
    node_role_scores,
    predict_rows,
    pretrain_node_model,
    save_model,
    train_model,
)


_LEARNED_ROLES = {"product", "dose", "frequency", "duration"}
_COUNTED_FIELDS = (
    "expected_rows",
    "predicted_rows",
    "matched_rows",
    "missing_rows",
    "unexpected_rows",
    "critical_field_exact",
    "critical_field_total",
    "false_exact_fields",
    "unresolved_fields",
    "critical_detection_matched",
    "critical_detection_total",
    "cross_medication_associations",
    "unproven_associations",
)


def _polygon(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("OCR region polygon must contain four points")
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("OCR region polygon point must contain x/y")
        points.append((float(point[0]), float(point[1])))
    return tuple(points)


def document_from_sample_result(sample: Mapping[str, Any], result: Mapping[str, Any]) -> LabeledDocument:
    if result.get("status") != "ok":
        raise ValueError(f"full-document result for {sample.get('id')} is not successful")
    predicted_regions = list(result.get("regions") or [])
    region_map = _match_regions(list(sample.get("regions") or []), predicted_regions)
    image = result.get("image")
    if not isinstance(image, Mapping):
        raise ValueError("full-document result is missing image metadata")
    width = float(image.get("width") or sample.get("width") or 0)
    height = float(image.get("height") or sample.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("full-document result has invalid image dimensions")

    nodes: list[LayoutNode] = []
    seen_ids: set[str] = set()
    for fallback_index, region in enumerate(predicted_regions, start=1):
        raw_index = int(region.get("index", fallback_index))
        box_id = f"region-{raw_index:04d}"
        if box_id in seen_ids:
            raise ValueError(f"duplicate OCR region id: {box_id}")
        seen_ids.add(box_id)
        matched = region_map.get(box_id)
        raw_role = str(matched.get("semantic_role")) if matched is not None else "other"
        role = raw_role if raw_role in _LEARNED_ROLES else "other"
        raw_group = str(matched.get("association_group")) if matched is not None else ""
        group = raw_group if role in _LEARNED_ROLES and raw_group and raw_group != "document" else None
        score = region.get("recognition_score", 0.0)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"OCR region {box_id} recognition score must be numeric")
        nodes.append(
            LayoutNode(
                box_id=box_id,
                text=str(region.get("text") or ""),
                confidence=float(score),
                polygon=_polygon(region.get("polygon")),
                role=role,
                group=group,
            )
        )
    return LabeledDocument(
        sample_id=str(sample.get("id") or ""),
        width=width,
        height=height,
        nodes=tuple(nodes),
        layout_family=str(sample.get("layout_family") or "unknown"),
        capture_profile=str(sample.get("capture_profile") or "unknown"),
    )


def learned_rows_for_result(
    sample: Mapping[str, Any],
    result: Mapping[str, Any],
    model: Mapping[str, object],
) -> list[dict[str, object]]:
    regions = list(result.get("regions") or [])
    if not recognition_quality(regions)["safe_for_structured_parsing"]:
        return []
    document = document_from_sample_result(sample, result)
    return predict_rows(model, document)


def evaluate_learned_result(
    sample: Mapping[str, Any],
    result: Mapping[str, Any],
    model: Mapping[str, object],
) -> dict[str, Any]:
    learned_result = {
        "regions": list(result.get("regions") or []),
        "medications": learned_rows_for_result(sample, result, model),
    }
    return evaluate_sample(sample, learned_result)


def aggregate_samples(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {key: sum(int(sample[key]) for sample in samples) for key in _COUNTED_FIELDS}
    expected_rows = totals["expected_rows"]
    critical_fields = totals["critical_field_total"]
    return {
        "sample_count": len(samples),
        "quality_pass_samples": sum(bool(sample["quality_pass"]) for sample in samples),
        "safety_pass_samples": sum(bool(sample["safety_pass"]) for sample in samples),
        "totals": totals,
        "metrics": {
            "row_recall": totals["matched_rows"] / expected_rows if expected_rows else 1.0,
            "critical_field_exact_accuracy": (
                totals["critical_field_exact"] / critical_fields if critical_fields else 1.0
            ),
        },
    }


def _role_metrics(model: Mapping[str, object], documents: Sequence[LabeledDocument]) -> dict[str, Any]:
    confusion = {actual: Counter() for actual in ROLE_LABELS}
    total = 0
    correct = 0
    for document in documents:
        scores = node_role_scores(model, document)
        for node in document.nodes:
            actual = node.role if node.role in ROLE_LABELS else "other"
            predicted = max(scores[node.box_id].items(), key=lambda item: (item[1], item[0]))[0]
            confusion[actual][predicted] += 1
            total += 1
            correct += int(actual == predicted)

    per_role: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for role in ROLE_LABELS:
        tp = confusion[role][role]
        fp = sum(confusion[other][role] for other in ROLE_LABELS if other != role)
        fn = sum(count for predicted, count in confusion[role].items() if predicted != role)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_role[role] = {"support": sum(confusion[role].values()), "precision": precision, "recall": recall, "f1": f1}
    return {
        "accuracy": correct / total if total else 1.0,
        "macro_f1": sum(f1_values) / len(f1_values),
        "per_role": per_role,
    }


def _edge_metrics(model: Mapping[str, object], documents: Sequence[LabeledDocument]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    threshold = float(model["thresholds"]["edge"])
    for document in documents:
        products = [node for node in document.nodes if node.role == "product" and node.group]
        fields = [node for node in document.nodes if node.role in {"dose", "frequency", "duration"} and node.group]
        for field in fields:
            for product in products:
                actual = product.group == field.group
                predicted = edge_score(
                    model,
                    document,
                    product,
                    field,
                    str(field.role),
                    candidate_products=products,
                ) >= threshold
                if actual and predicted:
                    tp += 1
                elif actual:
                    fn += 1
                elif predicted:
                    fp += 1
                else:
                    tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def load_semantic_examples(
    samples_path: str | Path,
    *,
    per_role: int = 2500,
    seed: int = 112,
) -> tuple[list[SemanticExample], dict[str, Any]]:
    if per_role <= 0:
        raise ValueError("semantic per_role must be positive")
    roles = (*sorted(_LEARNED_ROLES), "other")
    buckets: dict[str, list[SemanticExample]] = {role: [] for role in roles}
    seen = {role: 0 for role in roles}
    rngs = {role: random.Random(seed + index * 1009) for index, role in enumerate(roles)}
    path = Path(samples_path)
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            try:
                sample = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"semantic samples line {line_number} is invalid JSON") from exc
            if not isinstance(sample, Mapping):
                raise ValueError(f"semantic samples line {line_number} must be an object")
            tags_raw = sample.get("semantic_tags")
            if not isinstance(tags_raw, list):
                raise ValueError(f"semantic samples line {line_number} is missing semantic_tags")
            tags = {str(tag) for tag in tags_raw}
            critical = tags & _LEARNED_ROLES
            if len(critical) == 1:
                role = next(iter(critical))
            elif not critical:
                role = "other"
            else:
                continue
            text = str(sample.get("text") or "").strip()
            if not text:
                continue
            seen[role] += 1
            example = SemanticExample(text=text, role=role)
            bucket = buckets[role]
            if len(bucket) < per_role:
                bucket.append(example)
                continue
            replacement = rngs[role].randrange(seen[role])
            if replacement < per_role:
                bucket[replacement] = example

    missing = {role: len(bucket) for role, bucket in buckets.items() if len(bucket) < per_role}
    if missing:
        raise ValueError(f"semantic samples do not satisfy per-role minimum: {missing}")
    examples = [example for role in roles for example in buckets[role]]
    digest = hashlib.sha256()
    for example in examples:
        digest.update(example.role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(example.text.encode("utf-8"))
        digest.update(b"\n")
    return examples, {
        "samples_path": str(path),
        "per_role": per_role,
        "seed": seed,
        "role_counts": {role: len(buckets[role]) for role in roles},
        "selected_fingerprint": digest.hexdigest(),
        "eligible_seen": seen,
    }


def load_records(corpus_path: str | Path, results_root: str | Path) -> list[dict[str, Any]]:
    corpus = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    if not isinstance(corpus, Mapping) or not isinstance(corpus.get("samples"), list):
        raise ValueError("detection corpus must contain samples")
    root = Path(results_root)
    records: list[dict[str, Any]] = []
    for sample in corpus["samples"]:
        result_path = root / str(sample["id"]) / "result.json"
        if not result_path.is_file():
            raise FileNotFoundError(f"missing full-document result: {result_path}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        records.append(
            {
                "sample": sample,
                "result": result,
                "document": document_from_sample_result(sample, result),
            }
        )
    return records


def _evaluate_records(model: Mapping[str, object], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    learned_samples = [evaluate_learned_result(record["sample"], record["result"], model) for record in records]
    baseline_samples = [evaluate_sample(record["sample"], record["result"]) for record in records]
    documents = [record["document"] for record in records]
    return {
        "learned": aggregate_samples(learned_samples),
        "geometry_rule_v2": aggregate_samples(baseline_samples),
        "node_roles": _role_metrics(model, documents),
        "same_medication_edges": _edge_metrics(model, documents),
    }


def cross_validate(
    records: Sequence[Mapping[str, Any]],
    *,
    axis: str,
    epochs: int,
    seed: int,
    node_initializer: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    if axis not in {"layout_family", "capture_profile"}:
        raise ValueError("cross-validation axis must be layout_family or capture_profile")
    values = sorted({str(getattr(record["document"], axis)) for record in records})
    folds: list[dict[str, Any]] = []
    learned_samples: list[dict[str, Any]] = []
    baseline_samples: list[dict[str, Any]] = []
    role_weighted = 0.0
    edge_counts = Counter()
    total_nodes = 0
    for fold_index, value in enumerate(values):
        train_records = [record for record in records if str(getattr(record["document"], axis)) != value]
        test_records = [record for record in records if str(getattr(record["document"], axis)) == value]
        model = train_model(
            [record["document"] for record in train_records],
            epochs=epochs,
            seed=seed + fold_index,
            node_initializer=node_initializer,
        )
        evaluation = _evaluate_records(model, test_records)
        fold_learned = [evaluate_learned_result(record["sample"], record["result"], model) for record in test_records]
        fold_baseline = [evaluate_sample(record["sample"], record["result"]) for record in test_records]
        learned_samples.extend(fold_learned)
        baseline_samples.extend(fold_baseline)
        node_count = sum(len(record["document"].nodes) for record in test_records)
        role_weighted += evaluation["node_roles"]["accuracy"] * node_count
        total_nodes += node_count
        for key in ("tp", "fp", "fn", "tn"):
            edge_counts[key] += int(evaluation["same_medication_edges"][key])
        folds.append({"holdout": value, "train_samples": len(train_records), "test_samples": len(test_records), **evaluation})

    edge_precision = edge_counts["tp"] / (edge_counts["tp"] + edge_counts["fp"]) if edge_counts["tp"] + edge_counts["fp"] else 1.0
    edge_recall = edge_counts["tp"] / (edge_counts["tp"] + edge_counts["fn"]) if edge_counts["tp"] + edge_counts["fn"] else 1.0
    return {
        "axis": axis,
        "fold_count": len(folds),
        "folds": folds,
        "aggregate": {
            "learned": aggregate_samples(learned_samples),
            "geometry_rule_v2": aggregate_samples(baseline_samples),
            "node_role_accuracy": role_weighted / total_nodes if total_nodes else 1.0,
            "edge_precision": edge_precision,
            "edge_recall": edge_recall,
            "edge_f1": 2 * edge_precision * edge_recall / (edge_precision + edge_recall) if edge_precision + edge_recall else 0.0,
        },
    }


def run_benchmark(
    *,
    corpus_path: str | Path,
    results_root: str | Path,
    output_dir: str | Path,
    epochs: int = 60,
    seed: int = 112,
    semantic_samples_path: str | Path | None = None,
    semantic_per_role: int = 2500,
    semantic_epochs: int = 12,
) -> dict[str, Any]:
    records = load_records(corpus_path, results_root)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    semantic_metadata: dict[str, Any] | None = None
    node_initializer: Mapping[str, object] | None = None
    if semantic_samples_path is not None:
        semantic_examples, semantic_metadata = load_semantic_examples(
            semantic_samples_path,
            per_role=semantic_per_role,
            seed=seed,
        )
        node_initializer = pretrain_node_model(
            semantic_examples,
            epochs=semantic_epochs,
            seed=seed + 5000,
        )
        semantic_metadata = {
            **semantic_metadata,
            "pretrain_epochs": semantic_epochs,
            "pretrain_model_id": node_initializer["model_id"],
        }
    capture_cv = cross_validate(
        records,
        axis="capture_profile",
        epochs=epochs,
        seed=seed,
        node_initializer=node_initializer,
    )
    layout_cv = cross_validate(
        records,
        axis="layout_family",
        epochs=epochs,
        seed=seed + 1000,
        node_initializer=node_initializer,
    )
    final_model = train_model(
        [record["document"] for record in records],
        epochs=epochs,
        seed=seed + 2000,
        node_initializer=node_initializer,
    )
    if semantic_metadata is not None:
        final_model["semantic_pretraining"] = semantic_metadata
    model_path = output / "model.json"
    save_model(final_model, model_path)
    in_sample = _evaluate_records(final_model, records)
    report = {
        "schema_version": 1,
        "model_id": final_model["model_id"],
        "training_samples": len(records),
        "epochs": epochs,
        "seed": seed,
        "model_bytes": model_path.stat().st_size,
        "semantic_pretraining": semantic_metadata,
        "capture_profile_cv": capture_cv,
        "layout_family_cv": layout_cv,
        "in_sample": in_sample,
    }
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


__all__ = [
    "aggregate_samples",
    "cross_validate",
    "document_from_sample_result",
    "evaluate_learned_result",
    "learned_rows_for_result",
    "load_records",
    "load_semantic_examples",
    "run_benchmark",
]