from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from .dataset import Dataset, DatasetError


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must contain a JSON object")
    return value


def _label_row(sample: dict) -> str:
    return f"{sample['image']}\t{sample['text']}\n"


def _required_tags(values: list[str], label: str) -> list[str]:
    if not values or any(not isinstance(value, str) or not value for value in values):
        raise DatasetError(f"{label} must contain non-empty tag names")
    if len(values) != len(set(values)):
        raise DatasetError(f"{label} must not contain duplicate tags")
    return values


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def prepare_test_slices(
    *,
    dataset: Dataset,
    export_dir: str | Path,
    output_dir: str | Path,
    expected_group_by: str,
    required_semantic_tags: list[str],
    required_risk_tags: list[str],
) -> dict[str, object]:
    export_root = Path(export_dir).resolve()
    split = _read_json_object(export_root / "split.json", "Paddle export split")
    if split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("Paddle export split fingerprint does not match dataset")
    if split.get("group_by") != expected_group_by:
        raise DatasetError("Paddle export split holdout axis does not match requested evaluation")
    splits = split.get("splits")
    if not isinstance(splits, dict) or not isinstance(splits.get("test"), list):
        raise DatasetError("Paddle export split is missing test membership")
    test_ids = splits["test"]
    if not test_ids or any(not isinstance(sample_id, str) for sample_id in test_ids):
        raise DatasetError("Paddle export test membership is invalid")
    if len(test_ids) != len(set(test_ids)):
        raise DatasetError("Paddle export test membership contains duplicates")

    by_id = {sample["id"]: sample for sample in dataset.samples}
    missing_ids = [sample_id for sample_id in test_ids if sample_id not in by_id]
    if missing_ids:
        raise DatasetError(f"Paddle export test membership references unknown sample: {missing_ids[0]}")
    expected_test = "".join(_label_row(by_id[sample_id]) for sample_id in test_ids)
    test_labels = export_root / "test.txt"
    try:
        actual_test = test_labels.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"could not read Paddle export test labels: {exc}") from exc
    if actual_test != expected_test:
        raise DatasetError("Paddle export test labels do not match authoritative split membership and dataset labels")

    semantic_tags = _required_tags(required_semantic_tags, "required semantic tags")
    risk_tags = _required_tags(required_risk_tags, "required risk tags")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    result: dict[str, object] = {
        "schema_version": 1,
        "test_count": len(test_ids),
        "semantic": {},
        "risk": {},
    }
    for result_key, sample_key, tags in (
        ("semantic", "semantic_tags", semantic_tags),
        ("risk", "risk_tags", risk_tags),
    ):
        section: dict[str, dict[str, object]] = {}
        for tag in tags:
            selected = [by_id[sample_id] for sample_id in test_ids if tag in by_id[sample_id][sample_key]]
            if not selected:
                singular = "semantic" if result_key == "semantic" else "risk"
                raise DatasetError(f"required {singular} slice is empty: {tag}")
            path = output / f"{result_key}-{tag}.txt"
            _write_atomic(path, "".join(_label_row(sample) for sample in selected))
            section[tag] = {"count": len(selected), "label_file": str(path)}
        result[result_key] = section
    return result


def evaluate_test_slices(
    plan: dict[str, object],
    *,
    evaluate: Callable[[Path, str, str], dict[str, float]],
) -> dict[str, object]:
    result: dict[str, object] = {"schema_version": 1, "semantic": {}, "risk": {}}
    for section_name in ("semantic", "risk"):
        section = plan.get(section_name)
        if not isinstance(section, dict):
            raise DatasetError(f"slice plan is missing {section_name} section")
        metrics_section: dict[str, dict[str, object]] = {}
        for tag, entry in section.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("label_file"), str):
                raise DatasetError(f"slice plan entry is invalid: {section_name}.{tag}")
            label_file = Path(entry["label_file"])
            stem = f"{section_name}-{tag}"
            pretrained = evaluate(label_file, "pretrained", stem)
            best = evaluate(label_file, "checkpoint", stem)
            metrics_section[tag] = {
                "count": entry["count"],
                "pretrained": pretrained,
                "best": best,
                "delta": {
                    "acc": best["acc"] - pretrained["acc"],
                    "norm_edit_dis": best["norm_edit_dis"] - pretrained["norm_edit_dis"],
                },
            }
        result[section_name] = metrics_section
    return result