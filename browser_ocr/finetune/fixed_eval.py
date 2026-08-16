from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

from .dataset import Dataset, DatasetError
from .full_document import parse_recognition_rows
from .model_compat import audit_model_compatibility


FIXED_EVAL_POLICY_ID = "fixed-recognition-eval-v2"
OOD_POLICY_ID = "severe-motion-downscale-jpeg-v1"
CRITICAL_ROLES = ("product", "dose", "frequency", "duration")
REQUIRED_CROSS_SLICES = (
    "seen-drug-unseen-image",
    "unseen-drug-familiar-degradation",
    "unseen-drug-hard-in-domain",
    "unseen-drug-hard-ood",
)


def audit_fixed_eval_reference_compatibility(
    dataset: Dataset,
    dictionary_path: str | Path,
    *,
    max_text_length: int,
    use_space_char: bool,
) -> dict[str, object]:
    overall = audit_model_compatibility(
        dataset,
        dictionary_path,
        max_text_length=max_text_length,
        use_space_char=use_space_char,
    )
    critical_samples = tuple(
        sample for sample in dataset.samples if _contains(sample, "critical-medication")
    )
    if not critical_samples:
        raise DatasetError("fixed evaluation requires critical-medication samples")
    critical_dataset = Dataset(
        root=dataset.root,
        manifest_path=dataset.manifest_path,
        manifest=dataset.manifest,
        samples=critical_samples,
        fingerprint=dataset.fingerprint,
    )
    critical = audit_model_compatibility(
        critical_dataset,
        dictionary_path,
        max_text_length=max_text_length,
        use_space_char=use_space_char,
    )
    return {"schema_version": 1, "overall": overall, "critical": critical}


def _contains(sample: Mapping[str, object], tag: str, field: str = "risk_tags") -> bool:
    values = sample.get(field)
    return isinstance(values, list) and tag in values


def _semantic(sample: Mapping[str, object], role: str) -> bool:
    return _contains(sample, role, "semantic_tags")


def _slice_entry(sample_ids: Iterable[str]) -> dict[str, object]:
    ids = sorted(sample_ids)
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return {"count": len(ids), "sample_ids_sha256": digest, "sample_ids": ids}


def _augmentation_tags(sample: Mapping[str, object]) -> tuple[str, ...]:
    values = sample.get("risk_tags")
    if not isinstance(values, list):
        return ()
    return tuple(sorted(tag for tag in values if isinstance(tag, str) and tag.startswith("augmentation-")))


def _combo_slug(tags: tuple[str, ...]) -> str:
    if not tags:
        return "none"
    names = [tag.removeprefix("augmentation-") for tag in tags]
    return "+".join(names)


def _metadata(dataset: Dataset) -> tuple[dict, dict]:
    metadata = dataset.manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise DatasetError("fixed evaluation requires recognition manifest metadata")
    evaluation = metadata.get("recognition_evaluation_policy")
    if not isinstance(evaluation, dict) or evaluation.get("id") != OOD_POLICY_ID:
        raise DatasetError(f"fixed evaluation requires recognition evaluation policy {OOD_POLICY_ID}")
    drug = metadata.get("drug_name_policy")
    if not isinstance(drug, dict):
        raise DatasetError("fixed evaluation requires drug_name_policy metadata")
    if not isinstance(drug.get("assignment_seed"), int):
        raise DatasetError("drug_name_policy.assignment_seed must be an integer")
    assignment_sha = drug.get("assignment_sha256")
    if not isinstance(assignment_sha, str) or len(assignment_sha) != 64:
        raise DatasetError("drug_name_policy.assignment_sha256 is invalid")
    return evaluation, drug


def build_fixed_eval_plan(dataset: Dataset, *, minimum_required_count: int = 32) -> dict[str, object]:
    if not isinstance(minimum_required_count, int) or minimum_required_count <= 0:
        raise DatasetError("minimum_required_count must be a positive integer")
    evaluation_policy, drug_policy = _metadata(dataset)
    samples = list(dataset.samples)
    if not samples:
        raise DatasetError("fixed evaluation dataset is empty")

    by_id: dict[str, dict] = {}
    for sample in samples:
        sample_id = sample.get("id")
        if not isinstance(sample_id, str) or not sample_id:
            raise DatasetError("fixed evaluation sample is missing id")
        if sample_id in by_id:
            raise DatasetError(f"duplicate fixed evaluation sample id: {sample_id}")
        by_id[sample_id] = sample

    critical = [sample for sample in samples if _contains(sample, "critical-medication")]
    if not critical:
        raise DatasetError("fixed evaluation requires critical-medication samples")

    slices: dict[str, dict[str, object]] = {
        "overall": _slice_entry(sample["id"] for sample in samples),
        "critical": _slice_entry(sample["id"] for sample in critical),
    }
    for role in CRITICAL_ROLES:
        slices[f"role-{role}"] = _slice_entry(sample["id"] for sample in critical if _semantic(sample, role))
    for difficulty in ("clean", "medium", "hard"):
        slices[f"difficulty-{difficulty}"] = _slice_entry(
            sample["id"] for sample in critical if _contains(sample, f"difficulty-{difficulty}")
        )
    for exposure in ("seen", "unseen"):
        slices[f"drug-{exposure}"] = _slice_entry(
            sample["id"] for sample in critical if _contains(sample, f"drug-exposure-{exposure}")
        )

    for exposure in ("seen", "unseen"):
        slices[f"product-{exposure}"] = _slice_entry(
            sample["id"]
            for sample in critical
            if _semantic(sample, "product") and _contains(sample, f"drug-exposure-{exposure}")
        )

    slices["seen-drug-unseen-image"] = _slice_entry(
        sample["id"] for sample in critical if _contains(sample, "drug-exposure-seen")
    )
    slices["unseen-drug-familiar-degradation"] = _slice_entry(
        sample["id"]
        for sample in critical
        if _contains(sample, "drug-exposure-unseen")
        and (_contains(sample, "difficulty-clean") or _contains(sample, "difficulty-medium"))
    )
    slices["unseen-drug-hard-in-domain"] = _slice_entry(
        sample["id"]
        for sample in critical
        if _contains(sample, "drug-exposure-unseen")
        and _contains(sample, "difficulty-hard")
        and not _contains(sample, "degradation-hard-ood")
    )
    slices["unseen-drug-hard-ood"] = _slice_entry(
        sample["id"]
        for sample in critical
        if _contains(sample, "drug-exposure-unseen") and _contains(sample, "degradation-hard-ood")
    )

    component_members: dict[str, list[str]] = defaultdict(list)
    combination_members: dict[str, list[str]] = defaultdict(list)
    combination_tags: dict[str, tuple[str, ...]] = {}
    for sample in critical:
        tags = _augmentation_tags(sample)
        for tag in tags:
            component_members[tag.removeprefix("augmentation-")].append(sample["id"])
        combo = _combo_slug(tags)
        combination_members[combo].append(sample["id"])
        combination_tags[combo] = tags
    for component, ids in sorted(component_members.items()):
        slices[f"augmentation-component-{component}"] = _slice_entry(ids)
    for combo, ids in sorted(combination_members.items()):
        name_hash = hashlib.sha256(combo.encode("utf-8")).hexdigest()[:12]
        entry = _slice_entry(ids)
        entry["components"] = [tag.removeprefix("augmentation-") for tag in combination_tags[combo]]
        entry["combination"] = combo
        slices[f"augmentation-combination-{name_hash}"] = entry

    required = [
        "critical",
        *[f"role-{role}" for role in CRITICAL_ROLES],
        "difficulty-clean",
        "difficulty-medium",
        "difficulty-hard",
        "drug-seen",
        "drug-unseen",
        "product-seen",
        "product-unseen",
        *REQUIRED_CROSS_SLICES,
    ]
    for name in required:
        count = slices[name]["count"]
        if count < minimum_required_count:
            raise DatasetError(
                f"fixed evaluation required slice {name} has {count} samples; minimum is {minimum_required_count}"
            )

    return {
        "schema_version": 1,
        "policy_id": FIXED_EVAL_POLICY_ID,
        "dataset_id": dataset.manifest.get("dataset_id"),
        "dataset_fingerprint": dataset.fingerprint,
        "sample_count": len(samples),
        "critical_count": len(critical),
        "minimum_required_count": minimum_required_count,
        "recognition_evaluation_policy": evaluation_policy,
        "drug_assignment_seed": drug_policy["assignment_seed"],
        "drug_assignment_sha256": drug_policy["assignment_sha256"],
        "slices": slices,
    }


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def normalized_edit_similarity(reference: str, prediction: str) -> float:
    denominator = max(len(reference), len(prediction), 1)
    return 1.0 - (_levenshtein(reference, prediction) / denominator)


def _metrics(sample_ids: list[str], by_id: Mapping[str, dict], predictions: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    exact_count = 0
    similarity_total = 0.0
    confidence_total = 0.0
    for sample_id in sample_ids:
        sample = by_id[sample_id]
        prediction = predictions[sample_id]
        predicted_text = prediction.get("text")
        score = prediction.get("score")
        if not isinstance(predicted_text, str):
            raise DatasetError(f"prediction {sample_id}.text must be a string")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
            raise DatasetError(f"prediction {sample_id}.score must be between 0 and 1")
        reference = sample["text"]
        exact_count += int(predicted_text == reference)
        similarity_total += normalized_edit_similarity(reference, predicted_text)
        confidence_total += float(score)
    count = len(sample_ids)
    return {
        "count": count,
        "exact_count": exact_count,
        "exact_accuracy": exact_count / count if count else 0.0,
        "normalized_edit_similarity": similarity_total / count if count else 0.0,
        "mean_confidence": confidence_total / count if count else 0.0,
    }


def evaluate_fixed_predictions(
    dataset: Dataset,
    predictions: Mapping[str, Mapping[str, object]],
    plan: Mapping[str, object],
) -> dict[str, object]:
    if plan.get("policy_id") != FIXED_EVAL_POLICY_ID:
        raise DatasetError("fixed evaluation plan policy is unsupported")
    if plan.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("fixed evaluation plan dataset fingerprint mismatch")
    expected = {sample["id"] for sample in dataset.samples}
    actual = set(predictions)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = missing[0] if missing else extra[0] if extra else "unknown"
        raise DatasetError(f"prediction coverage does not match dataset: {detail}")
    by_id = {sample["id"]: sample for sample in dataset.samples}
    raw_slices = plan.get("slices")
    if not isinstance(raw_slices, dict):
        raise DatasetError("fixed evaluation plan is missing slices")
    evaluated: dict[str, dict[str, object]] = {}
    for name, entry in raw_slices.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sample_ids"), list):
            raise DatasetError(f"fixed evaluation slice {name} is invalid")
        ids = entry["sample_ids"]
        if any(not isinstance(sample_id, str) or sample_id not in by_id for sample_id in ids):
            raise DatasetError(f"fixed evaluation slice {name} references unknown samples")
        evaluated[name] = _metrics(ids, by_id, predictions)
    return {
        "schema_version": 1,
        "policy_id": FIXED_EVAL_POLICY_ID,
        "dataset_id": dataset.manifest.get("dataset_id"),
        "dataset_fingerprint": dataset.fingerprint,
        "overall": evaluated["overall"],
        "critical": evaluated["critical"],
        "slices": evaluated,
    }

def infer_list_text(dataset: Dataset) -> str:
    samples = sorted(dataset.samples, key=lambda sample: sample["id"])
    return "".join(f"{sample['image']}\n" for sample in samples)


def parse_infer_predictions(dataset: Dataset, output: str) -> dict[str, dict[str, object]]:
    path_to_id = {
        str((dataset.root / sample["image"]).resolve()): sample["id"]
        for sample in dataset.samples
    }
    parsed = parse_recognition_rows(output, path_to_id)
    return {
        path_to_id[path]: {"text": value["text"], "score": value["score"]}
        for path, value in parsed.items()
    }
