from __future__ import annotations

from pathlib import Path

from .dataset import Dataset, DatasetError
from .runner_io import json_file
from .training import build_training_overrides
from .training_view import TRAINING_VIEW_POLICY_ID


RECOGNIZER_EVAL_BATCH_STEP = 1000
TRAINING_VIEW_SPLIT_ASSIGNMENT = "parent_document_split_filtered_training_v1"


def _label_row(sample: dict) -> str:
    return f"{sample['image']}\t{sample['text']}\n"


def validate_recognizer_training_view(
    dataset: Dataset,
    export_dir: str | Path,
    *,
    expected_dictionary_sha256: str,
    expected_max_text_length: int,
    expected_use_space_char: bool,
) -> dict:
    metadata = dataset.manifest.get("metadata")
    policy = metadata.get("training_view_policy") if isinstance(metadata, dict) else None
    if not isinstance(policy, dict) or policy.get("policy_id") != TRAINING_VIEW_POLICY_ID:
        raise DatasetError("recognizer training requires the unified recognition training-view policy")
    if policy.get("dictionary_sha256") != expected_dictionary_sha256:
        raise DatasetError("training view dictionary SHA-256 does not match recognizer contract")
    if policy.get("max_text_length") != expected_max_text_length:
        raise DatasetError("training view max_text_length does not match recognizer contract")
    if policy.get("use_space_char") is not expected_use_space_char:
        raise DatasetError("training view use_space_char does not match recognizer contract")
    if policy.get("train_excluded_risk_tag") != "degradation-hard-ood":
        raise DatasetError("training view does not reserve the required severe OOD signature")

    root = Path(export_dir).resolve()
    export = json_file(root / "export.json")
    split = json_file(root / "split.json")
    if export.get("dataset_id") != dataset.manifest["dataset_id"] or export.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("training view Paddle export does not match dataset")
    if split.get("dataset_id") != dataset.manifest["dataset_id"] or split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("training view Paddle split does not match dataset")
    if export.get("group_by") != "document_id" or split.get("group_by") != "document_id":
        raise DatasetError("training view must inherit document_id split semantics")
    if split.get("assignment") != TRAINING_VIEW_SPLIT_ASSIGNMENT:
        raise DatasetError("training view split assignment is unsupported")

    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise DatasetError("training view split must contain train, val and test")
    counts = split.get("counts")
    if not isinstance(counts, dict) or any(
        not isinstance(counts.get(name), int) or counts[name] <= 0
        for name in ("train", "val", "test")
    ):
        raise DatasetError("training view split counts are invalid")
    if export.get("counts") != counts:
        raise DatasetError("training view export counts do not match split")

    by_id = {sample["id"]: sample for sample in dataset.samples}
    all_ids: list[str] = []
    for name in ("train", "val", "test"):
        ids = splits[name]
        if not isinstance(ids, list) or len(ids) != counts[name] or any(not isinstance(sample_id, str) for sample_id in ids):
            raise DatasetError(f"training view {name} membership is invalid")
        if len(ids) != len(set(ids)):
            raise DatasetError(f"training view {name} membership contains duplicates")
        unknown = [sample_id for sample_id in ids if sample_id not in by_id]
        if unknown:
            raise DatasetError(f"training view {name} references unknown sample {unknown[0]}")
        all_ids.extend(ids)
        expected = "".join(_label_row(by_id[sample_id]) for sample_id in ids)
        try:
            actual = (root / f"{name}.txt").read_text(encoding="utf-8")
        except OSError as exc:
            raise DatasetError(f"could not read training view {name} labels: {exc}") from exc
        if actual != expected:
            raise DatasetError(f"training view {name} labels do not match authoritative membership")

    if len(all_ids) != len(set(all_ids)) or set(all_ids) != set(by_id):
        raise DatasetError("training view split does not cover the derived dataset exactly once")
    for sample_id in splits["train"]:
        if "degradation-hard-ood" in by_id[sample_id].get("risk_tags", []):
            raise DatasetError(f"training split contains held-out OOD sample {sample_id}")
    return {"counts": counts, "split": split, "export": export}


def build_recognizer_training_overrides(
    *,
    dataset_root: Path,
    export_dir: Path,
    initial_checkpoint: Path,
    resume_checkpoint: Path | None,
    output_dir: Path,
    batch_size: int,
    epochs: int,
    learning_rate: float,
    warmup_epochs: int,
) -> dict[str, object]:
    overrides = build_training_overrides(
        dataset_root=str(dataset_root),
        train_labels=str(export_dir / "train.txt"),
        val_labels=str(export_dir / "val.txt"),
        pretrained_model=str(initial_checkpoint),
        checkpoint=str(resume_checkpoint) if resume_checkpoint is not None else None,
        output_dir=str(output_dir),
        batch_size=batch_size,
        epochs=epochs,
        learning_rate=learning_rate,
        warmup_epochs=warmup_epochs,
    )
    overrides["Global.eval_batch_step"] = [0, RECOGNIZER_EVAL_BATCH_STEP]
    return overrides


__all__ = [
    "RECOGNIZER_EVAL_BATCH_STEP",
    "build_recognizer_training_overrides",
    "validate_recognizer_training_view",
]