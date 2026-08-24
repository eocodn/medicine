from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import yaml


class DetectorTrainingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectorTrainingConfig:
    epochs: int = 6
    batch_size: int = 4
    learning_rate: float = 0.0001
    warmup_epochs: int = 1
    num_workers: int = 2

    def validate(self) -> None:
        if isinstance(self.epochs, bool) or not isinstance(self.epochs, int) or self.epochs <= 0:
            raise DetectorTrainingError("epochs must be a positive integer")
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int) or self.batch_size <= 0:
            raise DetectorTrainingError("batch size must be a positive integer")
        if not isinstance(self.learning_rate, (int, float)) or isinstance(self.learning_rate, bool):
            raise DetectorTrainingError("learning rate must be numeric")
        if not math.isfinite(float(self.learning_rate)) or float(self.learning_rate) <= 0:
            raise DetectorTrainingError("learning rate must be positive and finite")
        if (
            isinstance(self.warmup_epochs, bool)
            or not isinstance(self.warmup_epochs, int)
            or self.warmup_epochs < 0
            or self.warmup_epochs >= self.epochs
        ):
            raise DetectorTrainingError("warmup epochs must be non-negative and less than total epochs")
        if isinstance(self.num_workers, bool) or not isinstance(self.num_workers, int) or self.num_workers <= 0:
            raise DetectorTrainingError("num workers must be a positive integer")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict:
    if not path.is_file():
        raise DetectorTrainingError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DetectorTrainingError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise DetectorTrainingError(f"{label} must be a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _require_sha256(value: object, label: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise DetectorTrainingError(f"{label} must be a lowercase SHA-256")
    return text


def _verify_file(path: Path, expected_sha256: object, label: str, *, expected_bytes: object | None = None) -> str:
    expected = _require_sha256(expected_sha256, f"{label} SHA-256")
    if not path.is_file():
        raise DetectorTrainingError(f"{label} does not exist: {path}")
    if expected_bytes is not None:
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes <= 0:
            raise DetectorTrainingError(f"{label} expected byte size is invalid")
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            raise DetectorTrainingError(
                f"{label} byte size mismatch: expected {expected_bytes}, got {actual_bytes}"
            )
    actual = _sha256_file(path)
    if actual != expected:
        raise DetectorTrainingError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _resolved_child(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DetectorTrainingError(f"{label} path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DetectorTrainingError(f"{label} path must stay inside its export directory")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise DetectorTrainingError(f"{label} path escapes its export directory") from exc
    return resolved


def _validate_label_rows(path: Path, label: str, *, expected_images: set[str]) -> int:
    if not path.is_file():
        raise DetectorTrainingError(f"{label} labels do not exist: {path}")
    try:
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        raise DetectorTrainingError(f"could not read {label} labels: {path}") from exc
    if not rows:
        raise DetectorTrainingError(f"{label} labels are empty")
    observed_images: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        if "\t" not in row:
            raise DetectorTrainingError(f"{label} labels contain a malformed Paddle detection row")
        image_value, annotation_value = row.split("\t", 1)
        image_path = Path(image_value)
        if image_path.is_absolute() or not image_path.parts or ".." in image_path.parts:
            raise DetectorTrainingError(f"{label} label row {row_number} has an unsafe image path")
        try:
            annotations = json.loads(annotation_value)
        except json.JSONDecodeError as exc:
            raise DetectorTrainingError(f"{label} label row {row_number} has invalid annotation JSON") from exc
        if not isinstance(annotations, list):
            raise DetectorTrainingError(f"{label} label row {row_number} annotations must be a list")
        observed_images.append(image_path.as_posix())
    if len(observed_images) != len(set(observed_images)):
        raise DetectorTrainingError(f"{label} label image membership contains duplicates")
    if set(observed_images) != expected_images:
        raise DetectorTrainingError(f"{label} label image membership differs from the authoritative corpus split")
    return len(rows)


def _format_override(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return str(value)


def _bound_training_transforms(document_config: Path, *, epochs: int) -> list[object]:
    try:
        document = yaml.safe_load(document_config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DetectorTrainingError(f"document detector fine-tune config is invalid YAML: {document_config}") from exc
    if not isinstance(document, Mapping):
        raise DetectorTrainingError("document detector fine-tune config must be a mapping")
    global_config = document.get("Global")
    train = document.get("Train")
    if not isinstance(global_config, Mapping) or not isinstance(train, Mapping):
        raise DetectorTrainingError("document detector fine-tune config is missing Global or Train")
    base_epochs = global_config.get("epoch_num")
    dataset = train.get("dataset")
    if isinstance(base_epochs, bool) or not isinstance(base_epochs, int) or base_epochs <= 0:
        raise DetectorTrainingError("document detector fine-tune config has invalid Global.epoch_num")
    if not isinstance(dataset, Mapping) or not isinstance(dataset.get("transforms"), list):
        raise DetectorTrainingError("document detector fine-tune config is missing Train.dataset.transforms")

    # PaddleOCR resolves YAML anchors before applying `-o` CLI overrides. Therefore
    # overriding only Global.epoch_num does not update the epoch-aware DB target
    # transforms. Rebind the complete transform list so the target schedule and
    # optimizer schedule share one authoritative requested epoch count.
    transforms = json.loads(json.dumps(dataset["transforms"], ensure_ascii=False))
    schedule_names = {"MakeBorderMap", "MakeShrinkMap"}
    seen: set[str] = set()
    for item in transforms:
        if not isinstance(item, dict):
            continue
        for name in schedule_names:
            if name not in item:
                continue
            if name in seen:
                raise DetectorTrainingError(f"document detector fine-tune config has duplicate {name} transform")
            settings = item[name]
            if not isinstance(settings, dict):
                raise DetectorTrainingError(f"document detector fine-tune config has invalid {name} transform")
            if settings.get("total_epoch") != base_epochs:
                raise DetectorTrainingError(
                    f"document detector fine-tune config {name}.total_epoch must match Global.epoch_num"
                )
            settings["total_epoch"] = epochs
            seen.add(name)
    if seen != schedule_names:
        missing = ", ".join(sorted(schedule_names - seen))
        raise DetectorTrainingError(f"document detector fine-tune config is missing epoch-aware transforms: {missing}")
    return transforms


def _training_overrides(
    *,
    data_dir: Path,
    train_labels: Path,
    val_labels: Path,
    pretrained_model: Path,
    model_dir: Path,
    training_transforms: list[object],
    config: DetectorTrainingConfig,
    resume_checkpoint: Path | None,
) -> dict[str, object]:
    overrides: dict[str, object] = {
        "Global.save_model_dir": str(model_dir),
        "Global.epoch_num": config.epochs,
        "Global.print_batch_step": 10,
        "Global.save_epoch_step": 1,
        # DB detector train batches do not include the eval shape_list contract, so
        # PaddleOCR's in-train metric path is invalid here. Validation remains a
        # separate full Eval pass, once per epoch, and still selects best_accuracy.
        "Global.eval_batch_step": [0, 1],
        "Global.eval_batch_epoch": 1,
        "Global.cal_metric_during_train": False,
        "Global.distributed": False,
        "Global.use_gpu": True,
        "Optimizer.lr.learning_rate": float(config.learning_rate),
        "Optimizer.lr.warmup_epoch": config.warmup_epochs,
        "Train.dataset.data_dir": str(data_dir),
        "Train.dataset.label_file_list": [str(train_labels)],
        "Train.dataset.transforms": training_transforms,
        "Train.loader.batch_size_per_card": config.batch_size,
        "Train.loader.num_workers": config.num_workers,
        "Train.loader.shuffle": True,
        "Eval.dataset.data_dir": str(data_dir),
        "Eval.dataset.label_file_list": [str(val_labels)],
        "Eval.loader.batch_size_per_card": 1,
        "Eval.loader.num_workers": config.num_workers,
        "Eval.loader.shuffle": False,
    }
    if resume_checkpoint is None:
        overrides["Global.pretrained_model"] = str(pretrained_model)
    else:
        overrides["Global.checkpoints"] = str(resume_checkpoint)
    return overrides


def _training_command(
    *,
    paddleocr_root: Path,
    document_config: Path,
    overrides: Mapping[str, object],
) -> list[str]:
    train_script = paddleocr_root / "tools" / "train.py"
    if not train_script.is_file():
        raise DetectorTrainingError(f"PaddleOCR training script does not exist: {train_script}")
    command = [sys.executable, "tools/train.py", "-c", str(document_config), "-o"]
    command.extend(f"{key}={_format_override(value)}" for key, value in overrides.items())
    return command


def _load_inputs(
    *,
    upstream_path: Path,
    paddleocr_root: Path,
    pretrained_model: Path,
    corpus_manifest: Path,
    detection_export: Path,
    config: DetectorTrainingConfig,
) -> dict[str, object]:
    config.validate()
    upstream = _json_object(upstream_path, "detector training upstream contract")
    if upstream.get("schema_version") != 1 or upstream.get("framework") != "PaddleOCR":
        raise DetectorTrainingError("detector training upstream contract is unsupported")
    if upstream.get("detector") != "PP-OCRv5_mobile_det" or upstream.get("training_enabled") is not True:
        raise DetectorTrainingError("detector training upstream contract is not enabled for PP-OCRv5_mobile_det")

    paddle = upstream.get("paddleocr")
    if not isinstance(paddle, Mapping):
        raise DetectorTrainingError("detector training upstream contract is missing PaddleOCR metadata")
    commit = str(paddle.get("commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DetectorTrainingError("detector PaddleOCR commit is invalid")
    official_config_value = paddle.get("config_path")
    if not isinstance(official_config_value, str) or not official_config_value:
        raise DetectorTrainingError("detector PaddleOCR config path is missing")
    official_config = (paddleocr_root / official_config_value).resolve()
    try:
        official_config.relative_to(paddleocr_root)
    except ValueError as exc:
        raise DetectorTrainingError("detector PaddleOCR config path escapes PaddleOCR root") from exc
    official_sha = _verify_file(
        official_config,
        paddle.get("config_sha256"),
        "official PP-OCRv5 mobile detector config",
    )
    runtime_source_files = paddle.get("runtime_source_files")
    if not isinstance(runtime_source_files, list) or not runtime_source_files:
        raise DetectorTrainingError("detector PaddleOCR runtime source bindings are missing")
    verified_runtime_source_files: list[dict[str, str]] = []
    for index, source_binding in enumerate(runtime_source_files):
        if not isinstance(source_binding, Mapping):
            raise DetectorTrainingError(f"detector PaddleOCR runtime source binding {index} must be an object")
        raw_source_path = source_binding.get("path")
        if not isinstance(raw_source_path, str) or not raw_source_path:
            raise DetectorTrainingError(f"detector PaddleOCR runtime source binding {index} path is missing")
        source_path = Path(raw_source_path)
        if source_path.is_absolute() or not source_path.parts or ".." in source_path.parts:
            raise DetectorTrainingError(f"detector PaddleOCR runtime source binding {index} path is unsafe")
        resolved_source = (paddleocr_root / source_path).resolve()
        try:
            resolved_source.relative_to(paddleocr_root)
        except ValueError as exc:
            raise DetectorTrainingError(
                f"detector PaddleOCR runtime source binding {index} escapes PaddleOCR root"
            ) from exc
        source_sha = _verify_file(
            resolved_source,
            source_binding.get("sha256"),
            f"detector PaddleOCR runtime source {raw_source_path}",
        )
        verified_runtime_source_files.append({"path": source_path.as_posix(), "sha256": source_sha})

    document = upstream.get("document_config")
    if not isinstance(document, Mapping):
        raise DetectorTrainingError("detector training upstream contract is missing document config metadata")
    raw_document_path = document.get("path")
    if not isinstance(raw_document_path, str) or not raw_document_path:
        raise DetectorTrainingError("detector document config path is missing")
    document_config = Path(raw_document_path)
    if not document_config.is_absolute():
        document_config = upstream_path.parent / document_config
    document_config = document_config.resolve()
    document_sha = _verify_file(
        document_config,
        document.get("sha256"),
        "document detector fine-tune config",
    )
    pretrained_sha = _verify_file(
        pretrained_model,
        upstream.get("pretrained_model_sha256"),
        "detector pretrained model",
        expected_bytes=upstream.get("pretrained_model_bytes"),
    )

    corpus = _json_object(corpus_manifest, "unified OCR corpus manifest")
    if corpus.get("schema_version") != 3 or corpus.get("synthetic_only") is not True:
        raise DetectorTrainingError("detector fine-tuning requires a schema-v3 synthetic-only unified corpus")
    generator = corpus.get("generator")
    if not isinstance(generator, Mapping) or generator.get("version") != 6:
        raise DetectorTrainingError("detector fine-tuning requires unified generator v6")
    corpus_id = str(corpus.get("corpus_id") or "")
    samples = corpus.get("samples")
    if not corpus_id or not isinstance(samples, list) or not samples:
        raise DetectorTrainingError("unified OCR corpus is missing identity or samples")
    split_counts = {name: 0 for name in ("train", "val", "test")}
    split_images: dict[str, set[str]] = {name: set() for name in split_counts}
    seen_ids: set[str] = set()
    corpus_root = corpus_manifest.parent.resolve()
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise DetectorTrainingError(f"unified OCR corpus sample {index} must be an object")
        sample_id = str(sample.get("id") or "")
        split = str(sample.get("split") or "")
        if not sample_id or sample_id in seen_ids:
            raise DetectorTrainingError("unified OCR corpus sample ids must be non-empty and unique")
        if split not in split_counts:
            raise DetectorTrainingError(f"unified OCR corpus sample split is invalid for {sample_id}")
        raw_image = sample.get("image")
        if not isinstance(raw_image, str) or not raw_image:
            raise DetectorTrainingError(f"unified OCR corpus sample image is missing for {sample_id}")
        relative_image = Path(raw_image)
        if relative_image.is_absolute() or not relative_image.parts or ".." in relative_image.parts:
            raise DetectorTrainingError(f"unified OCR corpus sample image path is unsafe for {sample_id}")
        image_path = (corpus_root / relative_image).resolve()
        try:
            image_path.relative_to(corpus_root)
        except ValueError as exc:
            raise DetectorTrainingError(f"unified OCR corpus sample image escapes corpus root for {sample_id}") from exc
        _verify_file(image_path, sample.get("image_sha256"), f"unified OCR corpus image {sample_id}")
        seen_ids.add(sample_id)
        split_counts[split] += 1
        split_images[split].add(relative_image.as_posix())
    if any(count <= 0 for count in split_counts.values()):
        raise DetectorTrainingError("detector fine-tuning corpus must contain train, val, and test documents")
    corpus_sha = _sha256_file(corpus_manifest)

    export = _json_object(detection_export, "Paddle detection export")
    if export.get("schema_version") != 1 or export.get("task") != "text_detection":
        raise DetectorTrainingError("Paddle detection export contract is unsupported")
    if export.get("parent_corpus_id") != corpus_id:
        raise DetectorTrainingError("Paddle detection export corpus id does not match the unified corpus")
    if export.get("parent_corpus_sha256") != corpus_sha:
        raise DetectorTrainingError("Paddle detection export corpus SHA-256 does not match the unified corpus")
    if export.get("polygon_kind") != "region_polygon" or export.get("transcription_policy") != "ground_truth_text":
        raise DetectorTrainingError("Paddle detection export annotation policy is unsupported")
    counts = export.get("counts")
    if counts != split_counts:
        raise DetectorTrainingError("Paddle detection export split counts do not match the unified corpus")
    data_dir_value = export.get("data_dir")
    if not isinstance(data_dir_value, str) or not data_dir_value:
        raise DetectorTrainingError("Paddle detection export data_dir is missing")
    data_dir = Path(data_dir_value).resolve()
    if data_dir != corpus_manifest.parent.resolve():
        raise DetectorTrainingError("Paddle detection export data_dir must equal the unified corpus root")
    label_files = export.get("label_files")
    if not isinstance(label_files, Mapping) or set(label_files) != {"train", "val", "test"}:
        raise DetectorTrainingError("Paddle detection export must declare train, val, and test labels")
    labels = {
        split: _resolved_child(detection_export.parent, label_files[split], f"{split} label")
        for split in ("train", "val", "test")
    }
    label_counts = {
        split: _validate_label_rows(path, split, expected_images=split_images[split])
        for split, path in labels.items()
    }
    if label_counts != split_counts:
        raise DetectorTrainingError("Paddle detection label row counts do not match authoritative split counts")

    return {
        "upstream": upstream,
        "commit": commit,
        "official_config": official_config,
        "official_config_sha256": official_sha,
        "runtime_source_files": verified_runtime_source_files,
        "document_config": document_config,
        "document_config_sha256": document_sha,
        "training_transforms": _bound_training_transforms(document_config, epochs=config.epochs),
        "pretrained_model_sha256": pretrained_sha,
        "corpus": corpus,
        "corpus_id": corpus_id,
        "corpus_sha256": corpus_sha,
        "export": export,
        "export_sha256": _sha256_file(detection_export),
        "data_dir": data_dir,
        "labels": labels,
        "label_sha256": {name: _sha256_file(path) for name, path in labels.items()},
        "counts": split_counts,
    }


def _profile(
    *,
    upstream_path: Path,
    corpus_manifest: Path,
    detection_export: Path,
    inputs: Mapping[str, object],
    config: DetectorTrainingConfig,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "runner": "ppocrv5-mobile-document-detector-finetune-v2",
        "upstream_sha256": _sha256_file(upstream_path),
        "paddleocr_commit": inputs["commit"],
        "official_config_sha256": inputs["official_config_sha256"],
        "paddleocr_runtime_source_files": inputs["runtime_source_files"],
        "document_config_sha256": inputs["document_config_sha256"],
        "pretrained_model_sha256": inputs["pretrained_model_sha256"],
        "corpus": {
            "id": inputs["corpus_id"],
            "manifest_sha256": _sha256_file(corpus_manifest),
            "export_sha256": _sha256_file(detection_export),
            "counts": inputs["counts"],
            "label_sha256": inputs["label_sha256"],
        },
        "hyperparameters": asdict(config),
        "optimization_splits": ["train", "val"],
        "promotion_evaluation_split": "test",
    }


def prepare_detector_training(
    *,
    upstream_path: str | Path,
    paddleocr_root: str | Path,
    pretrained_model: str | Path,
    corpus_manifest: str | Path,
    detection_export: str | Path,
    run_dir: str | Path,
    config: DetectorTrainingConfig,
) -> dict[str, object]:
    upstream_path = Path(upstream_path).resolve()
    paddleocr_root = Path(paddleocr_root).resolve()
    pretrained_model = Path(pretrained_model).resolve()
    corpus_manifest = Path(corpus_manifest).resolve()
    detection_export = Path(detection_export).resolve()
    run_dir = Path(run_dir).resolve()
    inputs = _load_inputs(
        upstream_path=upstream_path,
        paddleocr_root=paddleocr_root,
        pretrained_model=pretrained_model,
        corpus_manifest=corpus_manifest,
        detection_export=detection_export,
        config=config,
    )
    profile = _profile(
        upstream_path=upstream_path,
        corpus_manifest=corpus_manifest,
        detection_export=detection_export,
        inputs=inputs,
        config=config,
    )
    model_dir = run_dir / "model"
    overrides = _training_overrides(
        data_dir=inputs["data_dir"],
        train_labels=inputs["labels"]["train"],
        val_labels=inputs["labels"]["val"],
        pretrained_model=pretrained_model,
        model_dir=model_dir,
        training_transforms=inputs["training_transforms"],
        config=config,
        resume_checkpoint=None,
    )
    command = _training_command(
        paddleocr_root=paddleocr_root,
        document_config=inputs["document_config"],
        overrides=overrides,
    )
    if any(str(inputs["labels"]["test"]) in item for item in command):
        raise DetectorTrainingError("test labels must never appear in detector optimization command")
    return {
        "schema_version": 1,
        "status": "ready",
        "profile": profile,
        "command": command,
        "run_dir": str(run_dir),
        "promotion": "requires_project_safety_evaluation",
    }



__all__ = [
    "DetectorTrainingConfig",
    "DetectorTrainingError",
    "prepare_detector_training",
]
