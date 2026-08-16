from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable

from .dataset import DatasetError, export_paddle, load_dataset
from .model_compat import audit_model_compatibility, classify_model_compatibility
from .runner_io import sha256_file


TRAINING_VIEW_POLICY_ID = "unified-recognition-training-view-v1"
OOD_POLICY_ID = "severe-motion-downscale-jpeg-v1"


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must contain a JSON object")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_hash(profile: dict) -> str:
    return hashlib.sha256(_canonical_json(profile).encode("utf-8")).hexdigest()


def _validate_source_split(dataset, split: dict) -> dict[str, str]:
    if split.get("schema_version") != 1:
        raise DatasetError("source recognition split schema_version is unsupported")
    if split.get("dataset_id") != dataset.manifest["dataset_id"]:
        raise DatasetError("source recognition split dataset_id mismatch")
    if split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("source recognition split dataset fingerprint mismatch")
    if split.get("group_by") != "document_id" or split.get("assignment") != "parent_document_split_v1":
        raise DatasetError("source recognition split must inherit parent document split")
    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise DatasetError("source recognition split must contain train, val and test")
    owner: dict[str, str] = {}
    for name in ("train", "val", "test"):
        ids = splits[name]
        if not isinstance(ids, list) or not ids or any(not isinstance(sample_id, str) for sample_id in ids):
            raise DatasetError(f"source recognition split {name} membership is invalid")
        for sample_id in ids:
            if sample_id in owner:
                raise DatasetError(f"source recognition split contains duplicate sample {sample_id}")
            owner[sample_id] = name
    expected = {sample["id"] for sample in dataset.samples}
    if set(owner) != expected:
        detail = sorted(expected.symmetric_difference(owner))[0]
        raise DatasetError(f"source recognition split coverage mismatch: {detail}")
    counts = split.get("counts")
    if not isinstance(counts, dict) or any(counts.get(name) != len(splits[name]) for name in ("train", "val", "test")):
        raise DatasetError("source recognition split counts are invalid")
    return owner


def _validate_source_metadata(dataset) -> None:
    metadata = dataset.manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise DatasetError("unified recognition manifest metadata is required")
    policy = metadata.get("recognition_evaluation_policy")
    if not isinstance(policy, dict) or policy.get("id") != OOD_POLICY_ID:
        raise DatasetError(f"training view requires recognition evaluation policy {OOD_POLICY_ID}")
    if not isinstance(metadata.get("drug_name_policy"), dict):
        raise DatasetError("training view requires drug_name_policy metadata")


def _cached_result(output: Path, profile: dict, dictionary_path: Path) -> dict | None:
    if not output.exists():
        return None
    if not output.is_dir():
        raise DatasetError(f"training view output is not a directory: {output}")
    report_path = output / "training-view.json"
    if not report_path.is_file():
        raise DatasetError("training view output exists without authoritative report")
    report = _read_json_object(report_path, "training view report")
    if report.get("profile") != profile:
        raise DatasetError("completed training view profile does not match requested profile")
    dataset = load_dataset(output / "manifest.json")
    if dataset.fingerprint != report.get("dataset_fingerprint"):
        raise DatasetError("completed training view dataset fingerprint mismatch")
    compatibility = audit_model_compatibility(
        dataset,
        dictionary_path,
        max_text_length=profile["max_text_length"],
        use_space_char=profile["use_space_char"],
    )
    if compatibility["status"] != "ok":
        raise DatasetError("completed training view is incompatible with pinned recognizer contract")
    export = _read_json_object(output / "paddle" / "export.json", "training view Paddle export")
    if export.get("dataset_fingerprint") != dataset.fingerprint or export.get("counts") != report.get("retained_counts"):
        raise DatasetError("completed training view Paddle export does not match dataset")
    return report


def prepare_unified_training_view(
    *,
    manifest_path: str | Path,
    split_path: str | Path,
    output_dir: str | Path,
    dictionary_path: str | Path,
    dictionary_sha256: str,
    max_text_length: int,
    use_space_char: bool,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    manifest = Path(manifest_path).resolve()
    source_split_path = Path(split_path).resolve()
    dictionary = Path(dictionary_path).resolve()
    output = Path(output_dir).resolve()
    if not dictionary.is_file() or sha256_file(dictionary) != dictionary_sha256:
        raise DatasetError("training view dictionary SHA-256 mismatch")
    source = load_dataset(manifest)
    _validate_source_metadata(source)
    try:
        split_bytes = source_split_path.read_bytes()
    except OSError as exc:
        raise DatasetError(f"could not read source recognition split {source_split_path}: {exc}") from exc
    split = _read_json_object(source_split_path, "source recognition split")
    owner = _validate_source_split(source, split)
    split_sha256 = hashlib.sha256(split_bytes).hexdigest()
    profile = {
        "schema_version": 1,
        "policy_id": TRAINING_VIEW_POLICY_ID,
        "source_dataset_id": source.manifest["dataset_id"],
        "source_dataset_fingerprint": source.fingerprint,
        "source_split_sha256": split_sha256,
        "dictionary_sha256": dictionary_sha256,
        "max_text_length": max_text_length,
        "use_space_char": use_space_char,
        "train_excluded_risk_tag": "degradation-hard-ood",
        "model_incompatible_samples": "exclude-all-splits",
    }
    profile_sha256 = _profile_hash(profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"training view preparation is already active for {output}") from exc
        cached = _cached_result(output, profile, dictionary)
        if cached is not None:
            return cached

        issues = classify_model_compatibility(
            source,
            dictionary,
            max_text_length=max_text_length,
            use_space_char=use_space_char,
        )
        retained: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
        excluded = Counter()
        for sample in source.samples:
            sample_id = sample["id"]
            sample_split = owner[sample_id]
            reasons = issues.get(sample_id, ())
            for reason in reasons:
                excluded[reason] += 1
            train_ood = sample_split == "train" and "degradation-hard-ood" in sample["risk_tags"]
            if train_ood:
                excluded["train_ood"] += 1
            if reasons or train_ood:
                continue
            retained[sample_split].append(sample)
        if any(not retained[name] for name in ("train", "val", "test")):
            raise DatasetError("training view filtering produced an empty split")

        stage = output.parent / f".{output.name}.stage-{uuid.uuid4().hex}"
        if stage.exists():
            shutil.rmtree(stage)
        try:
            stage.mkdir(parents=True)
            total = sum(len(items) for items in retained.values())
            written = 0
            samples_path = stage / "samples.jsonl"
            with samples_path.open("w", encoding="utf-8", newline="\n") as handle:
                for split_name in ("train", "val", "test"):
                    for sample in retained[split_name]:
                        source_image = source.root / sample["image"]
                        target_image = stage / sample["image"]
                        target_image.parent.mkdir(parents=True, exist_ok=True)
                        os.link(source_image, target_image)
                        handle.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
                        written += 1
                        if progress and (written == total or written % 1000 == 0):
                            progress(written, total)
            source_metadata = source.manifest.get("metadata") or {}
            derived_manifest = {
                "schema_version": 1,
                "dataset_id": f"unified-training-{source.fingerprint[:20]}",
                "task": "text_recognition",
                "patient_data_policy": "forbid",
                "samples_file": "samples.jsonl",
                "description": "Model-compatible unified recognition training view with severe OOD held out from optimization",
                "metadata": {
                    **source_metadata,
                    "training_view_policy": {
                        **profile,
                        "profile_sha256": profile_sha256,
                        "excluded": dict(sorted(excluded.items())),
                        "retained_counts": {name: len(retained[name]) for name in ("train", "val", "test")},
                    },
                },
            }
            (stage / "manifest.json").write_text(
                json.dumps(derived_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            derived = load_dataset(stage / "manifest.json")
            compatibility = audit_model_compatibility(
                derived,
                dictionary,
                max_text_length=max_text_length,
                use_space_char=use_space_char,
            )
            if compatibility["status"] != "ok":
                raise DatasetError("derived training view is incompatible with pinned recognizer contract")
            derived_split = {
                "schema_version": 1,
                "dataset_id": derived.manifest["dataset_id"],
                "dataset_fingerprint": derived.fingerprint,
                "group_by": "document_id",
                "seed": split["seed"],
                "ratios": split["ratios"],
                "component_count": split.get("component_count", 0),
                "max_component_size": split.get("max_component_size", 0),
                "counts": {name: len(retained[name]) for name in ("train", "val", "test")},
                "splits": {name: sorted(sample["id"] for sample in retained[name]) for name in ("train", "val", "test")},
                "assignment": "parent_document_split_filtered_training_v1",
            }
            export = export_paddle(derived, derived_split, stage / "paddle", data_dir_override=output)
            report = {
                "schema_version": 1,
                "status": "completed",
                "profile": profile,
                "profile_sha256": profile_sha256,
                "dataset_id": derived.manifest["dataset_id"],
                "dataset_fingerprint": derived.fingerprint,
                "source_sample_count": len(source.samples),
                "retained_counts": derived_split["counts"],
                "retained_sample_count": len(derived.samples),
                "excluded": dict(sorted(excluded.items())),
                "excluded_sample_count": len(source.samples) - len(derived.samples),
                "compatibility": compatibility,
                "paddle_export": export,
            }
            (stage / "training-view.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(stage, output)
            return report
        finally:
            if stage.exists():
                shutil.rmtree(stage)
