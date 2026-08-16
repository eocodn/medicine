from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from .dataset import Dataset, DatasetError, export_paddle, load_dataset
from .model_compat import audit_model_compatibility
from .runner_io import json_file, sha256_file
from .training_view import TRAINING_VIEW_POLICY_ID


MIXED_TRAINING_VIEW_POLICY_ID = "selected-checkpoint-mixed-training-view-v1"
MIXED_SPLIT_ASSIGNMENT = "historical-train-plus-unified-train-new-eval-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _profile_hash(profile: dict) -> str:
    return hashlib.sha256(_canonical_json(profile).encode("utf-8")).hexdigest()


def _namespace_slug(prefix: str, value: str) -> str:
    candidate = f"{prefix}-{value}"
    if len(candidate) <= 128:
        return candidate
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _remap_sample(sample: dict, *, prefix: str, image_path: str) -> dict:
    return {
        **sample,
        "id": _namespace_slug(prefix, sample["id"]),
        "image": image_path,
        "document_id": _namespace_slug(prefix, sample["document_id"]),
        "groups": {
            key: _namespace_slug(prefix, value)
            for key, value in sample["groups"].items()
        },
    }


def _validate_historical_split(dataset: Dataset, split: dict) -> list[str]:
    if split.get("schema_version") != 1:
        raise DatasetError("historical recognition split schema_version is unsupported")
    if split.get("dataset_id") != dataset.manifest["dataset_id"]:
        raise DatasetError("historical recognition split dataset_id mismatch")
    if split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("historical recognition split fingerprint mismatch")
    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise DatasetError("historical recognition split must contain train, val and test")
    all_ids = [sample_id for name in ("train", "val", "test") for sample_id in splits[name]]
    expected = {sample["id"] for sample in dataset.samples}
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != expected:
        raise DatasetError("historical recognition split does not cover source dataset exactly once")
    counts = split.get("counts")
    if not isinstance(counts, dict) or any(counts.get(name) != len(splits[name]) for name in ("train", "val", "test")):
        raise DatasetError("historical recognition split counts are invalid")
    if not splits["train"]:
        raise DatasetError("historical recognition train split is empty")
    return list(splits["train"])


def _validate_unified_source(dataset: Dataset, export_dir: Path) -> tuple[dict, dict]:
    metadata = dataset.manifest.get("metadata")
    policy = metadata.get("training_view_policy") if isinstance(metadata, dict) else None
    if not isinstance(policy, dict) or policy.get("policy_id") != TRAINING_VIEW_POLICY_ID:
        raise DatasetError(f"mixed training requires source policy {TRAINING_VIEW_POLICY_ID}")
    split = json_file(export_dir / "split.json")
    export = json_file(export_dir / "export.json")
    if split.get("dataset_id") != dataset.manifest["dataset_id"] or split.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("unified training split does not match dataset")
    if export.get("dataset_id") != dataset.manifest["dataset_id"] or export.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("unified training export does not match dataset")
    if split.get("group_by") != "document_id" or split.get("assignment") != "parent_document_split_filtered_training_v1":
        raise DatasetError("unified training split assignment is unsupported")
    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise DatasetError("unified training split must contain train, val and test")
    all_ids = [sample_id for name in ("train", "val", "test") for sample_id in splits[name]]
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != {sample["id"] for sample in dataset.samples}:
        raise DatasetError("unified training split does not cover dataset exactly once")
    if export.get("counts") != split.get("counts"):
        raise DatasetError("unified training export counts do not match split")
    return split, policy


def _subset(dataset: Dataset, sample_ids: set[str]) -> Dataset:
    return Dataset(
        root=dataset.root,
        manifest_path=dataset.manifest_path,
        manifest=dataset.manifest,
        samples=tuple(sample for sample in dataset.samples if sample["id"] in sample_ids),
        fingerprint=dataset.fingerprint,
    )


def _cached_result(output: Path, profile: dict, dictionary: Path) -> dict | None:
    if not output.exists():
        return None
    if not output.is_dir():
        raise DatasetError(f"mixed training output is not a directory: {output}")
    report_path = output / "mixed-training-view.json"
    if not report_path.is_file():
        raise DatasetError("mixed training output exists without authoritative report")
    report = json_file(report_path)
    if report.get("profile") != profile:
        raise DatasetError("completed mixed training view profile does not match requested profile")
    dataset = load_dataset(output / "manifest.json")
    if dataset.fingerprint != report.get("dataset_fingerprint"):
        raise DatasetError("completed mixed training dataset fingerprint mismatch")
    compatibility = audit_model_compatibility(
        dataset,
        dictionary,
        max_text_length=profile["max_text_length"],
        use_space_char=profile["use_space_char"],
    )
    if compatibility["status"] != "ok":
        raise DatasetError("completed mixed training view is incompatible with recognizer contract")
    export = json_file(output / "paddle" / "export.json")
    if export.get("dataset_fingerprint") != dataset.fingerprint or export.get("counts") != report.get("retained_counts"):
        raise DatasetError("completed mixed training Paddle export does not match dataset")
    return report


def prepare_mixed_training_view(
    *,
    historical_manifest_path: str | Path,
    historical_split_path: str | Path,
    unified_manifest_path: str | Path,
    unified_export_dir: str | Path,
    output_dir: str | Path,
    dictionary_path: str | Path,
    dictionary_sha256: str,
    max_text_length: int,
    use_space_char: bool,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    historical_manifest = Path(historical_manifest_path).resolve()
    historical_split_file = Path(historical_split_path).resolve()
    unified_manifest = Path(unified_manifest_path).resolve()
    unified_export = Path(unified_export_dir).resolve()
    output = Path(output_dir).resolve()
    dictionary = Path(dictionary_path).resolve()
    if not dictionary.is_file() or sha256_file(dictionary) != dictionary_sha256:
        raise DatasetError("mixed training dictionary SHA-256 mismatch")

    historical = load_dataset(historical_manifest)
    unified = load_dataset(unified_manifest)
    try:
        historical_split_bytes = historical_split_file.read_bytes()
    except OSError as exc:
        raise DatasetError(f"could not read historical recognition split {historical_split_file}: {exc}") from exc
    historical_split = json_file(historical_split_file)
    historical_train_ids = _validate_historical_split(historical, historical_split)
    historical_split_sha256 = hashlib.sha256(historical_split_bytes).hexdigest()
    unified_split, unified_policy = _validate_unified_source(unified, unified_export)

    metadata = unified.manifest.get("metadata")
    drug_policy = metadata.get("drug_name_policy") if isinstance(metadata, dict) else None
    exposure = drug_policy.get("historical_exposure") if isinstance(drug_policy, dict) else None
    if not isinstance(exposure, dict):
        raise DatasetError("unified drug holdout is missing historical exposure binding")
    if exposure.get("source_dataset_id") != historical.manifest["dataset_id"]:
        raise DatasetError("unified historical exposure source dataset does not match mixed historical source")
    if exposure.get("source_dataset_fingerprint") != historical.fingerprint:
        raise DatasetError("unified historical exposure fingerprint does not match mixed historical source")
    if exposure.get("source_train_split_sha256") != historical_split_sha256:
        raise DatasetError("unified historical exposure train split does not match mixed historical split")
    if exposure.get("source_train_sample_count") != len(historical_train_ids):
        raise DatasetError("unified historical exposure train count does not match mixed historical split")

    historical_train = _subset(historical, set(historical_train_ids))
    for label, dataset in (("historical train", historical_train), ("unified training view", unified)):
        compatibility = audit_model_compatibility(
            dataset,
            dictionary,
            max_text_length=max_text_length,
            use_space_char=use_space_char,
        )
        if compatibility["status"] != "ok":
            raise DatasetError(f"{label} is incompatible with selected recognizer contract")
    by_unified_id = {sample["id"]: sample for sample in unified.samples}
    for sample_id in unified_split["splits"]["train"]:
        if "degradation-hard-ood" in by_unified_id[sample_id]["risk_tags"]:
            raise DatasetError(f"unified training split contains held-out OOD sample {sample_id}")

    profile = {
        "schema_version": 1,
        "policy_id": MIXED_TRAINING_VIEW_POLICY_ID,
        "dictionary_sha256": dictionary_sha256,
        "max_text_length": max_text_length,
        "use_space_char": use_space_char,
        "train_excluded_risk_tag": "degradation-hard-ood",
        "mix_rule": MIXED_SPLIT_ASSIGNMENT,
        "historical": {
            "dataset_id": historical.manifest["dataset_id"],
            "dataset_fingerprint": historical.fingerprint,
            "train_split_sha256": historical_split_sha256,
            "train_count": len(historical_train_ids),
            "image_materialization": "copy-readonly-cross-device-source-v1",
        },
        "unified": {
            "dataset_id": unified.manifest["dataset_id"],
            "dataset_fingerprint": unified.fingerprint,
            "split_sha256": sha256_file(unified_export / "split.json"),
            "training_view_profile_sha256": unified_policy.get("profile_sha256"),
            "counts": unified_split["counts"],
            "image_materialization": "hardlink-current-workspace-v1",
        },
    }
    profile_sha256 = _profile_hash(profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"mixed training view preparation is already active for {output}") from exc
        cached = _cached_result(output, profile, dictionary)
        if cached is not None:
            return cached

        stage = output.parent / f".{output.name}.stage-{uuid.uuid4().hex}"
        if stage.exists():
            shutil.rmtree(stage)
        try:
            stage.mkdir(parents=True)
            historical_by_id = {sample["id"]: sample for sample in historical.samples}
            remapped: list[dict] = []
            split_ids: dict[str, list[str]] = {"train": [], "val": [], "test": []}
            total = len(historical_train_ids) + len(unified.samples)
            done = 0

            for sample_id in historical_train_ids:
                source = historical_by_id[sample_id]
                suffix = Path(source["image"]).suffix.lower()
                image_relative = f"images/historical/{source['id']}{suffix}"
                image_target = stage / image_relative
                image_target.parent.mkdir(parents=True, exist_ok=True)
                # The real historical corpus is mounted read-only at a separate Docker bind mount.
                # Hard-linking across that mount is EXDEV, so copying is the explicit deterministic boundary.
                shutil.copyfile(historical.root / source["image"], image_target)
                mapped = _remap_sample(source, prefix="hist", image_path=image_relative)
                remapped.append(mapped)
                split_ids["train"].append(mapped["id"])
                done += 1
                if progress and (done == total or done % 1000 == 0):
                    progress(done, total)

            for split_name in ("train", "val", "test"):
                for sample_id in unified_split["splits"][split_name]:
                    source = by_unified_id[sample_id]
                    suffix = Path(source["image"]).suffix.lower()
                    image_relative = f"images/unified/{source['id']}{suffix}"
                    image_target = stage / image_relative
                    image_target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(unified.root / source["image"], image_target)
                    mapped = _remap_sample(source, prefix="unified", image_path=image_relative)
                    remapped.append(mapped)
                    split_ids[split_name].append(mapped["id"])
                    done += 1
                    if progress and (done == total or done % 1000 == 0):
                        progress(done, total)

            (stage / "samples.jsonl").write_text(
                "".join(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n" for sample in remapped),
                encoding="utf-8",
            )
            source_metadata = unified.manifest.get("metadata") or {}
            derived_manifest = {
                "schema_version": 1,
                "dataset_id": f"mixed-training-{profile_sha256[:20]}",
                "task": "text_recognition",
                "patient_data_policy": "forbid",
                "samples_file": "samples.jsonl",
                "description": "Historical selected-100k train plus unified full-document train; unified val/test retained",
                "metadata": {
                    **source_metadata,
                    "training_view_policy": {
                        **profile,
                        "profile_sha256": profile_sha256,
                        "retained_counts": {name: len(split_ids[name]) for name in ("train", "val", "test")},
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
                raise DatasetError("derived mixed training view is incompatible with selected recognizer contract")
            derived_split = {
                "schema_version": 1,
                "dataset_id": derived.manifest["dataset_id"],
                "dataset_fingerprint": derived.fingerprint,
                "group_by": "document_id",
                "seed": unified_split["seed"],
                "ratios": unified_split.get("ratios", {"train": 0.8, "val": 0.1, "test": 0.1}),
                "component_count": 0,
                "max_component_size": 0,
                "counts": {name: len(split_ids[name]) for name in ("train", "val", "test")},
                "splits": {name: sorted(split_ids[name]) for name in ("train", "val", "test")},
                "assignment": MIXED_SPLIT_ASSIGNMENT,
            }
            export = export_paddle(derived, derived_split, stage / "paddle", data_dir_override=output)
            report = {
                "schema_version": 1,
                "status": "completed",
                "profile": profile,
                "profile_sha256": profile_sha256,
                "dataset_id": derived.manifest["dataset_id"],
                "dataset_fingerprint": derived.fingerprint,
                "retained_counts": derived_split["counts"],
                "retained_sample_count": len(derived.samples),
                "compatibility": compatibility,
                "paddle_export": export,
            }
            (stage / "mixed-training-view.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(stage, output)
            return report
        finally:
            if stage.exists():
                shutil.rmtree(stage)
