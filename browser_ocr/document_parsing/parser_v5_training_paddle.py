from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from .artifact_storage import exclusive_output_lock
from .parser_v5_dataset import ParserV5Dataset, load_parser_v5_dataset
from .parser_v5_encoder_paddle import (
    ParserV5EncoderSpec,
    ParserV5GlobalEncoder,
    masked_multilabel_role_loss,
    parser_v5_tensors,
)
from .parser_v5_heads_paddle import (
    ParserV5SemanticAssignmentHead,
    parser_v5_head_loss,
    parser_v5_head_targets,
)
from .parser_v5_model_input import build_parser_v5_model_input
from .parser_v5_structured_targets import build_parser_v5_structured_targets


STATE_FILE = "training-state.json"
RESULT_FILE = "result.json"
CHECKPOINTS_DIR = "checkpoints"


@dataclass(frozen=True)
class ParserV5TrainingConfig:
    epochs: int = 12
    learning_rate: float = 0.002
    weight_decay: float = 1e-4
    seed: int = 112
    max_text_bytes: int = 96
    hidden_dim: int = 96
    text_embedding_dim: int = 32
    text_conv_dim: int = 48
    layers: int = 2
    heads: int = 4
    feedforward_multiplier: int = 2
    assignment_hidden_dim: int = 64
    role_embedding_dim: int = 16
    semantic_loss_weight: float = 1.0
    head_loss_weight: float = 1.0
    device: str = "gpu"

    def __post_init__(self) -> None:
        if isinstance(self.epochs, bool) or not isinstance(self.epochs, int) or self.epochs <= 0:
            raise ValueError("Parser v5 training epochs must be a positive integer")
        for name in ("learning_rate", "semantic_loss_weight", "head_loss_weight"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"Parser v5 training {name} must be positive and finite")
        if not math.isfinite(float(self.weight_decay)) or self.weight_decay < 0:
            raise ValueError("Parser v5 training weight_decay must be non-negative and finite")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("Parser v5 training seed must be an integer")
        if isinstance(self.max_text_bytes, bool) or not 4 <= self.max_text_bytes <= 512:
            raise ValueError("Parser v5 training max_text_bytes must be between 4 and 512")
        if self.device not in {"cpu", "gpu"}:
            raise ValueError("Parser v5 training device must be cpu or gpu")
        self.encoder_spec
        if not 16 <= self.assignment_hidden_dim <= 256:
            raise ValueError("Parser v5 assignment_hidden_dim must be between 16 and 256")
        if not 4 <= self.role_embedding_dim <= 64:
            raise ValueError("Parser v5 role_embedding_dim must be between 4 and 64")

    @property
    def encoder_spec(self) -> ParserV5EncoderSpec:
        return ParserV5EncoderSpec(
            hidden_dim=self.hidden_dim,
            text_embedding_dim=self.text_embedding_dim,
            text_conv_dim=self.text_conv_dim,
            layers=self.layers,
            heads=self.heads,
            feedforward_multiplier=self.feedforward_multiplier,
        )


class ParserV5Model(nn.Layer):
    def __init__(self, config: ParserV5TrainingConfig) -> None:
        super().__init__()
        self.encoder = ParserV5GlobalEncoder(config.encoder_spec)
        self.heads = ParserV5SemanticAssignmentHead(
            hidden_dim=config.hidden_dim,
            assignment_hidden_dim=config.assignment_hidden_dim,
            role_embedding_dim=config.role_embedding_dim,
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(_canonical_json(value) + b"\n")
    os.replace(temporary, path)


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5 training JSON {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Parser v5 training JSON must be an object: {path}")
    return value


def _datasets(manifests: Sequence[str | Path], *, label: str) -> list[ParserV5Dataset]:
    if not manifests:
        raise ValueError(f"Parser v5 training requires at least one {label} dataset")
    datasets = [load_parser_v5_dataset(path) for path in manifests]
    identities = [(dataset.dataset_id, dataset.samples_sha256) for dataset in datasets]
    if len(identities) != len(set(identities)):
        raise ValueError(f"Parser v5 {label} datasets must be unique")
    return datasets


def _profile(
    train: Sequence[ParserV5Dataset],
    validation: Sequence[ParserV5Dataset],
    config: ParserV5TrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_id": "parser_v5_global_structured_v1",
        "train_datasets": [
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256} for dataset in train
        ],
        "validation_datasets": [
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256} for dataset in validation
        ],
        "config": asdict(config),
    }


def _sample_tensors(sample: Mapping[str, Any], config: ParserV5TrainingConfig):
    truth = sample["truth"]
    observation = sample["observation"]
    model_input = build_parser_v5_model_input(truth, observation, max_text_bytes=config.max_text_bytes)
    if not model_input.node_ids:
        return None
    tensors = parser_v5_tensors(model_input)
    structured = build_parser_v5_structured_targets(truth, observation)
    targets = parser_v5_head_targets(structured, node_count=len(model_input.node_ids))
    return tensors, targets


def _train_epoch(
    model: ParserV5Model,
    optimizer: paddle.optimizer.Optimizer,
    datasets: Sequence[ParserV5Dataset],
    config: ParserV5TrainingConfig,
    *,
    epoch: int,
) -> tuple[float, int]:
    samples = [(dataset.dataset_id, sample) for dataset in datasets for sample in dataset.samples]
    rng = random.Random(config.seed + epoch * 1_000_003)
    rng.shuffle(samples)
    model.train()
    total_loss = 0.0
    steps = 0
    for _, sample in samples:
        prepared = _sample_tensors(sample, config)
        if prepared is None:
            continue
        tensors, targets = prepared
        hidden, role_logits = model.encoder(tensors)
        candidate_logits, assignment_logits = model.heads(hidden, tensors.relation_features, targets)
        semantic = masked_multilabel_role_loss(role_logits, tensors)
        heads = parser_v5_head_loss(candidate_logits, assignment_logits, targets)
        loss = semantic * config.semantic_loss_weight + heads * config.head_loss_weight
        if not bool(paddle.isfinite(loss).item()):
            raise ValueError(f"non-finite Parser v5 training loss at epoch {epoch}")
        loss.backward()
        # Paddle names AdamW accumulator tensors lazily on the first step.
        # Guard the global unique-name counter so an in-process resume produces
        # the same optimizer-state keys as the checkpoint being restored.
        with paddle.utils.unique_name.guard():
            optimizer.step()
        optimizer.clear_grad()
        total_loss += float(loss.item())
        steps += 1
    if steps == 0:
        raise ValueError("Parser v5 training data produced no observable OCR nodes")
    return total_loss / steps, steps


def _binary_counts(logits: paddle.Tensor, targets: paddle.Tensor, mask: paddle.Tensor) -> tuple[int, int, int, int]:
    predicted = (F.sigmoid(logits) >= 0.5).astype("int64").numpy().reshape([-1]).tolist()
    actual = targets.astype("int64").numpy().reshape([-1]).tolist()
    active = mask.numpy().reshape([-1]).tolist()
    tp = fp = fn = correct = 0
    for guess, truth, enabled in zip(predicted, actual, active, strict=True):
        if float(enabled) <= 0:
            continue
        correct += int(int(guess) == int(truth))
        tp += int(int(guess) == 1 and int(truth) == 1)
        fp += int(int(guess) == 1 and int(truth) == 0)
        fn += int(int(guess) == 0 and int(truth) == 1)
    return tp, fp, fn, correct


@paddle.no_grad()
def evaluate_parser_v5(
    model: ParserV5Model,
    datasets: Sequence[ParserV5Dataset],
    config: ParserV5TrainingConfig,
) -> dict[str, float | int]:
    model.eval()
    role_tp = role_fp = role_fn = role_active = role_correct = 0
    candidate_correct = candidate_total = 0
    assignment_correct = assignment_total = 0
    documents = 0
    for dataset in datasets:
        for sample in dataset.samples:
            prepared = _sample_tensors(sample, config)
            if prepared is None:
                continue
            tensors, targets = prepared
            hidden, role_logits = model.encoder(tensors)
            candidate_logits, assignment_logits = model.heads(hidden, tensors.relation_features, targets)
            tp, fp, fn, correct = _binary_counts(role_logits, tensors.role_targets, tensors.role_mask)
            role_tp += tp
            role_fp += fp
            role_fn += fn
            role_correct += correct
            role_active += int(tensors.role_mask.sum().item())

            candidate_guess = (F.sigmoid(candidate_logits) >= 0.5).astype("int64")
            candidate_truth = targets.candidate_targets.astype("int64")
            candidate_active = targets.candidate_mask > 0
            candidate_correct += int(((candidate_guess == candidate_truth) & candidate_active).astype("int64").sum().item())
            candidate_total += int(candidate_active.astype("int64").sum().item())

            if targets.assignment_targets.shape[0] > 0:
                assignment_guess = paddle.argmax(assignment_logits, axis=1)
                active = targets.assignment_mask > 0
                assignment_correct += int(((assignment_guess == targets.assignment_targets) & active).astype("int64").sum().item())
                assignment_total += int(active.astype("int64").sum().item())
            documents += 1
    precision = role_tp / max(role_tp + role_fp, 1)
    recall = role_tp / max(role_tp + role_fn, 1)
    role_f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    return {
        "documents": documents,
        "role_micro_f1": role_f1,
        "role_element_accuracy": role_correct / max(role_active, 1),
        "candidate_accuracy": candidate_correct / max(candidate_total, 1),
        "assignment_accuracy": assignment_correct / max(assignment_total, 1),
        "assignment_supervised": assignment_total,
    }


def _selection(metrics: Mapping[str, Any]) -> float:
    return float(metrics["worst_view_score"])


def evaluate_parser_v5_views(
    model: ParserV5Model,
    datasets: Sequence[ParserV5Dataset],
    config: ParserV5TrainingConfig,
) -> dict[str, Any]:
    overall = evaluate_parser_v5(model, datasets, config)
    views: dict[str, dict[str, float | int]] = {}
    scores: list[float] = []
    for dataset in datasets:
        metrics = evaluate_parser_v5(model, [dataset], config)
        views[dataset.dataset_id] = metrics
        applicable = [float(metrics["role_micro_f1"]), float(metrics["candidate_accuracy"])]
        if int(metrics["assignment_supervised"]) > 0:
            applicable.append(float(metrics["assignment_accuracy"]))
        scores.append(sum(applicable) / len(applicable))
    if not scores:
        raise ValueError("Parser v5 validation produced no development views")
    return {
        **overall,
        "views": views,
        "worst_view_score": min(scores),
    }


def _checkpoint_record(root: Path, epoch: int, profile_sha256: str) -> dict[str, Any] | None:
    directory = root / CHECKPOINTS_DIR / f"epoch-{epoch:04d}"
    if not directory.exists():
        return None
    record_path = directory / "checkpoint.json"
    model_path = directory / "model.pdparams"
    optimizer_path = directory / "optimizer.pdopt"
    if not record_path.is_file() or not model_path.is_file() or not optimizer_path.is_file():
        raise ValueError(f"Parser v5 checkpoint {epoch} is incomplete")
    record = _json_file(record_path)
    if record.get("epoch") != epoch or record.get("profile_sha256") != profile_sha256:
        raise ValueError(f"Parser v5 checkpoint {epoch} profile mismatch")
    if record.get("model_sha256") != _sha256_file(model_path) or record.get("optimizer_sha256") != _sha256_file(optimizer_path):
        raise ValueError(f"Parser v5 checkpoint {epoch} SHA-256 mismatch")
    return {
        "epoch": epoch,
        "training_loss": float(record["training_loss"]),
        "training_steps": int(record["training_steps"]),
        "validation": dict(record["validation"]),
        "selection_score": float(record["selection_score"]),
        "checkpoint": str(model_path),
        "model_sha256": str(record["model_sha256"]),
        "optimizer_sha256": str(record["optimizer_sha256"]),
    }


def _save_checkpoint(
    root: Path,
    *,
    epoch: int,
    profile_sha256: str,
    model: ParserV5Model,
    optimizer: paddle.optimizer.Optimizer,
    training_loss: float,
    training_steps: int,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoints = root / CHECKPOINTS_DIR
    checkpoints.mkdir(parents=True, exist_ok=True)
    final = checkpoints / f"epoch-{epoch:04d}"
    if final.exists():
        raise ValueError(f"Parser v5 checkpoint already exists unexpectedly: {final}")
    temporary = checkpoints / f".epoch-{epoch:04d}.tmp-{os.getpid()}"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    try:
        model_path = temporary / "model.pdparams"
        optimizer_path = temporary / "optimizer.pdopt"
        paddle.save(model.state_dict(), str(model_path))
        paddle.save(optimizer.state_dict(), str(optimizer_path))
        record = {
            "schema_version": 1,
            "epoch": epoch,
            "profile_sha256": profile_sha256,
            "training_loss": training_loss,
            "training_steps": training_steps,
            "validation": dict(validation),
            "selection_score": _selection(validation),
            "model_sha256": _sha256_file(model_path),
            "optimizer_sha256": _sha256_file(optimizer_path),
        }
        _atomic_json(temporary / "checkpoint.json", record)
        os.replace(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    persisted = _checkpoint_record(root, epoch, profile_sha256)
    if persisted is None:
        raise AssertionError("Parser v5 checkpoint disappeared after commit")
    return persisted


def _adopt_checkpoints(root: Path, state: dict[str, Any], profile_sha256: str, epochs: int) -> list[dict[str, Any]]:
    history = list(state.get("history") or [])
    completed = int(state.get("completed_epoch") or 0)
    if len(history) != completed:
        raise ValueError("Parser v5 training state history disagrees with completed_epoch")
    for epoch in range(1, completed + 1):
        persisted = _checkpoint_record(root, epoch, profile_sha256)
        if persisted is None or persisted != history[epoch - 1]:
            raise ValueError(f"Parser v5 training state disagrees with checkpoint {epoch}")
    while completed < epochs:
        persisted = _checkpoint_record(root, completed + 1, profile_sha256)
        if persisted is None:
            break
        history.append(persisted)
        completed += 1
    state["completed_epoch"] = completed
    state["history"] = history
    return history


def _load_checkpoint(root: Path, history: Sequence[Mapping[str, Any]], model: ParserV5Model, optimizer) -> None:
    if not history:
        return
    epoch = int(history[-1]["epoch"])
    directory = root / CHECKPOINTS_DIR / f"epoch-{epoch:04d}"
    model.set_state_dict(paddle.load(str(directory / "model.pdparams")))
    optimizer.set_state_dict(paddle.load(str(directory / "optimizer.pdopt")))


def _completed_result(root: Path, profile: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    result_path = root / RESULT_FILE
    if not result_path.is_file() or state.get("result_sha256") != _sha256_file(result_path):
        raise ValueError("completed Parser v5 training result SHA-256 mismatch")
    result = _json_file(result_path)
    if result.get("status") != "ok" or result.get("profile") != profile:
        raise ValueError("completed Parser v5 training result profile mismatch")
    best = Path(str(result.get("best_checkpoint") or ""))
    if not best.is_file() or result.get("best_checkpoint_sha256") != _sha256_file(best):
        raise ValueError("completed Parser v5 best checkpoint SHA-256 mismatch")
    return result


def train_parser_v5(
    *,
    train_manifests: Sequence[str | Path],
    validation_manifests: Sequence[str | Path],
    output_dir: str | Path,
    config: ParserV5TrainingConfig = ParserV5TrainingConfig(),
) -> dict[str, Any]:
    train = _datasets(train_manifests, label="train")
    validation = _datasets(validation_manifests, label="validation")
    train_hashes = {dataset.samples_sha256 for dataset in train}
    if any(dataset.samples_sha256 in train_hashes for dataset in validation):
        raise ValueError("Parser v5 validation dataset must be disjoint from training data")
    profile = _profile(train, validation, config)
    profile_sha256 = _sha256_bytes(_canonical_json(profile))
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paddle.seed(config.seed)
    paddle.set_device(config.device)

    with exclusive_output_lock(root):
        state_path = root / STATE_FILE
        if state_path.exists():
            state = _json_file(state_path)
            if state.get("profile_sha256") != profile_sha256 or state.get("profile") != profile:
                raise ValueError("Parser v5 training profile does not match existing output")
            if state.get("status") == "completed":
                return _completed_result(root, profile, state)
            if state.get("status") != "running":
                raise ValueError("Parser v5 training state status is invalid")
        else:
            state = {
                "schema_version": 1,
                "status": "running",
                "profile": profile,
                "profile_sha256": profile_sha256,
                "completed_epoch": 0,
                "history": [],
            }
            _atomic_json(state_path, state)

        with paddle.utils.unique_name.guard():
            model = ParserV5Model(config)
            optimizer = paddle.optimizer.AdamW(
                learning_rate=config.learning_rate,
                parameters=model.parameters(),
                weight_decay=config.weight_decay,
            )
        history = _adopt_checkpoints(root, state, profile_sha256, config.epochs)
        _atomic_json(state_path, state)
        _load_checkpoint(root, history, model, optimizer)

        for epoch in range(len(history) + 1, config.epochs + 1):
            training_loss, steps = _train_epoch(model, optimizer, train, config, epoch=epoch)
            metrics = evaluate_parser_v5_views(model, validation, config)
            record = _save_checkpoint(
                root,
                epoch=epoch,
                profile_sha256=profile_sha256,
                model=model,
                optimizer=optimizer,
                training_loss=training_loss,
                training_steps=steps,
                validation=metrics,
            )
            history.append(record)
            state["completed_epoch"] = epoch
            state["history"] = history
            _atomic_json(state_path, state)

        best = max(history, key=lambda item: (float(item["selection_score"]), -int(item["epoch"])))
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "profile_sha256": profile_sha256,
            "history": history,
            "best_epoch": int(best["epoch"]),
            "best_validation": dict(best["validation"]),
            "best_checkpoint": str(best["checkpoint"]),
            "best_checkpoint_sha256": str(best["model_sha256"]),
        }
        _atomic_json(root / RESULT_FILE, result)
        state["status"] = "completed"
        state["result_sha256"] = _sha256_file(root / RESULT_FILE)
        _atomic_json(state_path, state)
        return result


__all__ = [
    "ParserV5Model",
    "ParserV5TrainingConfig",
    "evaluate_parser_v5",
    "evaluate_parser_v5_views",
    "train_parser_v5",
]