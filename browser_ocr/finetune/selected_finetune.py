from __future__ import annotations

import argparse
import fcntl
import shutil
import sys
from pathlib import Path

from .dataset import Dataset, DatasetError, load_dataset
from .full_document_cli import load_selected_recognizer
from .mixed_training_view import MIXED_SPLIT_ASSIGNMENT, MIXED_TRAINING_VIEW_POLICY_ID
from .model_compat import audit_model_compatibility
from .runner_io import json_file, sha256_file, stream_command, verify_sha, write_json_atomic
from .training import (
    build_baseline_overrides,
    export_identity,
    find_resume_checkpoint,
    format_paddle_override,
    parse_eval_metrics,
)
from .training_view import TRAINING_VIEW_POLICY_ID

SELECTED_EVAL_BATCH_STEP = 1000


def _label_row(sample: dict) -> str:
    return f"{sample['image']}\t{sample['text']}\n"


def validate_selected_training_view(
    dataset: Dataset,
    export_dir: str | Path,
    *,
    expected_dictionary_sha256: str,
    expected_max_text_length: int,
    expected_use_space_char: bool,
) -> dict:
    metadata = dataset.manifest.get("metadata")
    policy = metadata.get("training_view_policy") if isinstance(metadata, dict) else None
    policy_id = policy.get("policy_id") if isinstance(policy, dict) else None
    allowed_policies = {TRAINING_VIEW_POLICY_ID, MIXED_TRAINING_VIEW_POLICY_ID}
    if policy_id not in allowed_policies:
        raise DatasetError(
            "selected fine-tune requires a supported unified or mixed training view policy"
        )
    if policy.get("dictionary_sha256") != expected_dictionary_sha256:
        raise DatasetError("training view dictionary SHA-256 does not match selected recognizer contract")
    if policy.get("max_text_length") != expected_max_text_length:
        raise DatasetError("training view max_text_length does not match selected recognizer contract")
    if policy.get("use_space_char") is not expected_use_space_char:
        raise DatasetError("training view use_space_char does not match selected recognizer contract")
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
    expected_assignment = (
        "parent_document_split_filtered_training_v1"
        if policy_id == TRAINING_VIEW_POLICY_ID
        else MIXED_SPLIT_ASSIGNMENT
    )
    if split.get("assignment") != expected_assignment:
        raise DatasetError("training view split assignment is unsupported")
    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise DatasetError("training view split must contain train, val and test")
    counts = split.get("counts")
    if not isinstance(counts, dict) or any(not isinstance(counts.get(name), int) or counts[name] <= 0 for name in ("train", "val", "test")):
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


def build_selected_training_overrides(
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
    overrides = build_baseline_overrides(
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
    # Unified/mixed validation is large enough that evaluating every 100 steps
    # dominates wall time. Both #161 candidates use the same slower-changing
    # cadence while retaining the mandatory evaluation at step zero.
    overrides["Global.eval_batch_step"] = [0, SELECTED_EVAL_BATCH_STEP]
    return overrides


def _evaluate_model(
    *,
    source_root: Path,
    config_path: Path,
    dataset_root: Path,
    labels: Path,
    batch_size: int,
    log_path: Path,
    checkpoint: Path,
) -> dict[str, float]:
    overrides: dict[str, object] = {
        "Global.use_gpu": True,
        "Global.distributed": False,
        "Global.checkpoints": str(checkpoint),
        "Eval.dataset.data_dir": str(dataset_root),
        "Eval.dataset.label_file_list": [str(labels)],
        "Eval.loader.batch_size_per_card": batch_size,
        "Eval.loader.num_workers": 2,
        "Eval.loader.shuffle": False,
    }
    command = [sys.executable, "tools/eval.py", "-c", str(config_path), "-o"]
    command.extend(f"{key}={format_paddle_override(value)}" for key, value in overrides.items())
    return parse_eval_metrics(stream_command(command, cwd=source_root, log_path=log_path))


def run_selected_finetune(args: argparse.Namespace) -> dict:
    upstream = json_file(Path(args.upstream).resolve())
    if upstream.get("pin_status") != "training-smoke-verified" or not upstream.get("training_enabled"):
        raise DatasetError("training runtime has not passed the pinned smoke gate")
    source_root = Path(args.paddleocr_root).resolve()
    paddle_info = upstream["paddleocr"]
    dictionary = source_root / paddle_info["dictionary_path"]
    verify_sha(dictionary, paddle_info["dictionary_sha256"], "PaddleOCR dictionary")

    selected = load_selected_recognizer(args.initial_baseline_result)
    if args.expected_initial_checkpoint_sha256 and selected["checkpoint_sha256"] != args.expected_initial_checkpoint_sha256:
        raise DatasetError("selected initial checkpoint SHA-256 does not match --expected-initial-checkpoint-sha256")
    selected_profile = selected["result"].get("profile")
    if not isinstance(selected_profile, dict) or selected_profile.get("upstream_commit") != paddle_info["commit"]:
        raise DatasetError("selected initial checkpoint was not produced from the pinned PaddleOCR source")

    dataset = load_dataset(args.manifest)
    contract = upstream["model_contract"]
    compatibility = audit_model_compatibility(
        dataset,
        dictionary,
        max_text_length=contract["max_text_length"],
        use_space_char=contract["use_space_char"],
    )
    if compatibility["status"] != "ok":
        raise DatasetError("selected fine-tune dataset is incompatible with pinned recognizer contract")
    export_dir = Path(args.export_dir).resolve()
    view = validate_selected_training_view(
        dataset,
        export_dir,
        expected_dictionary_sha256=paddle_info["dictionary_sha256"],
        expected_max_text_length=contract["max_text_length"],
        expected_use_space_char=contract["use_space_char"],
    )
    if not isinstance(args.epochs, int) or args.epochs <= 0:
        raise DatasetError("epochs must be a positive integer")
    if not isinstance(args.batch_size, int) or args.batch_size <= 0:
        raise DatasetError("batch size must be a positive integer")
    if not isinstance(args.learning_rate, float) or args.learning_rate <= 0:
        raise DatasetError("learning rate must be positive")
    if not isinstance(args.warmup_epochs, int) or args.warmup_epochs < 0 or args.warmup_epochs >= args.epochs:
        raise DatasetError("warmup epochs must be non-negative and less than total epochs")

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "schema_version": 1,
        "runner": "selected-checkpoint-unified-finetune-v1",
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "export_counts": view["counts"],
        "export_identity": export_identity(export_dir),
        "training_view_profile_sha256": dataset.manifest["metadata"]["training_view_policy"].get("profile_sha256"),
        "upstream_commit": paddle_info["commit"],
        "dictionary_sha256": paddle_info["dictionary_sha256"],
        "initial_baseline_result_sha256": sha256_file(selected["result_path"]),
        "initial_checkpoint_sha256": selected["checkpoint_sha256"],
        "initial_config_sha256": selected["config_sha256"],
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "warmup_epochs": args.warmup_epochs,
        "eval_batch_step": SELECTED_EVAL_BATCH_STEP,
    }
    state_path = run_dir / "selected-finetune-state.json"
    result_path = run_dir / "baseline-result.json"
    model_dir = run_dir / "model"
    with (run_dir / ".selected-finetune.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"selected fine-tune is already active in {run_dir}") from exc

        if result_path.exists():
            result = json_file(result_path)
            if result.get("profile") != profile:
                raise DatasetError("completed selected fine-tune profile does not match requested configuration")
            verify_sha(Path(result["best_checkpoint"]), result["best_checkpoint_sha256"], "selected fine-tune best checkpoint")
            return result
        if state_path.exists():
            state = json_file(state_path)
            if state.get("profile") != profile:
                raise DatasetError("selected fine-tune state profile does not match requested configuration")
        else:
            if model_dir.exists():
                raise DatasetError("selected fine-tune model directory exists without authoritative state")
            state = {"schema_version": 1, "status": "initializing", "profile": profile}
            write_json_atomic(state_path, state)

        initial_eval_path = run_dir / "initial-test.json"
        if initial_eval_path.exists():
            initial_eval = json_file(initial_eval_path)
            if initial_eval.get("profile") != profile:
                raise DatasetError("cached selected initial evaluation profile mismatch")
        else:
            state["status"] = "evaluating-initial"
            write_json_atomic(state_path, state)
            initial_metrics = _evaluate_model(
                source_root=source_root,
                config_path=selected["config"],
                dataset_root=dataset.root,
                labels=export_dir / "test.txt",
                batch_size=args.batch_size,
                log_path=run_dir / "initial-test.log",
                checkpoint=selected["checkpoint"],
            )
            initial_eval = {"schema_version": 1, "profile": profile, "metrics": initial_metrics}
            write_json_atomic(initial_eval_path, initial_eval)

        resume = find_resume_checkpoint(model_dir)
        if resume is None and model_dir.exists():
            shutil.rmtree(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        overrides = build_selected_training_overrides(
            dataset_root=dataset.root,
            export_dir=export_dir,
            initial_checkpoint=selected["checkpoint"],
            resume_checkpoint=resume,
            output_dir=model_dir,
            batch_size=args.batch_size,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            warmup_epochs=args.warmup_epochs,
        )
        command = [sys.executable, "tools/train.py", "-c", str(selected["config"]), "-o"]
        command.extend(f"{key}={format_paddle_override(value)}" for key, value in overrides.items())
        state.update({"status": "training", "resume_checkpoint": str(resume) if resume else None, "command": command})
        write_json_atomic(state_path, state)
        try:
            stream_command(
                command,
                cwd=source_root,
                log_path=run_dir / "train.log",
                capture=False,
                append=resume is not None,
            )
        except DatasetError:
            state["status"] = "failed"
            recovered = find_resume_checkpoint(model_dir)
            state["recoverable_checkpoint"] = str(recovered) if recovered else None
            write_json_atomic(state_path, state)
            raise

        best_prefix = model_dir / "best_accuracy"
        best_checkpoint = Path(str(best_prefix) + ".pdparams")
        if not best_checkpoint.is_file():
            raise DatasetError("selected fine-tune completed without best_accuracy.pdparams")
        final_epoch = model_dir / f"iter_epoch_{args.epochs}"
        if not all(Path(str(final_epoch) + suffix).is_file() for suffix in (".pdparams", ".pdopt", ".states")):
            raise DatasetError(f"selected fine-tune did not persist a complete epoch {args.epochs} checkpoint")
        config_copy = model_dir / "config.yml"
        if not config_copy.is_file():
            raise DatasetError("selected fine-tune did not persist model/config.yml")

        state["status"] = "evaluating-best"
        state["best_checkpoint"] = str(best_checkpoint)
        write_json_atomic(state_path, state)
        best_metrics = _evaluate_model(
            source_root=source_root,
            config_path=config_copy,
            dataset_root=dataset.root,
            labels=export_dir / "test.txt",
            batch_size=args.batch_size,
            log_path=run_dir / "best-test.log",
            checkpoint=best_prefix,
        )
        initial_metrics = initial_eval["metrics"]
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "initial_test": initial_metrics,
            "best_test": best_metrics,
            "delta": {
                "acc": best_metrics["acc"] - initial_metrics["acc"],
                "norm_edit_dis": best_metrics["norm_edit_dis"] - initial_metrics["norm_edit_dis"],
            },
            "best_checkpoint": str(best_checkpoint),
            "best_checkpoint_sha256": sha256_file(best_checkpoint),
            "best_config_sha256": sha256_file(config_copy),
            "final_epoch_checkpoint": str(final_epoch) + ".pdparams",
            "final_epoch_checkpoint_sha256": sha256_file(Path(str(final_epoch) + ".pdparams")),
            "train_log": str(run_dir / "train.log"),
        }
        write_json_atomic(result_path, result)
        state["status"] = "completed"
        write_json_atomic(state_path, state)
        return result
