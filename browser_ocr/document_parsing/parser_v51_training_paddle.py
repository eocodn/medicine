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
import paddle.nn.functional as F

from .artifact_storage import exclusive_output_lock
from .parser_v5_dataset import ParserV5Dataset, load_parser_v5_dataset
from .parser_v51_loss_paddle import match_parser_v51_rows, parser_v51_set_loss
from .parser_v51_model_paddle import ParserV51Model, ParserV51ModelConfig, prepare_parser_v51_sample
from .parser_v51_targets import ROW_FIELD_ROLES, required_field_pieces


STATE_FILE = "training-state.json"
RESULT_FILE = "result.json"
CHECKPOINTS_DIR = "checkpoints"
_IMPLEMENTATION_FILES = (
    "parser_v5_model_input.py",
    "parser_v5_document_encoder_paddle.py",
    "parser_v51_targets.py",
    "parser_v51_direct_decoder_paddle.py",
    "parser_v51_loss_paddle.py",
    "parser_v51_model_paddle.py",
    "parser_v51_training_paddle.py",
)


@dataclass(frozen=True)
class ParserV51TrainingConfig:
    epochs: int = 8
    learning_rate: float = 0.001
    weight_decay: float = 1e-4
    seed: int = 62451
    max_text_bytes: int = 96
    hidden_dim: int = 96
    text_embedding_dim: int = 32
    text_conv_dim: int = 48
    layers: int = 2
    heads: int = 4
    feedforward_multiplier: int = 2
    max_rows: int = 8
    device: str = "gpu"

    def __post_init__(self) -> None:
        if isinstance(self.epochs, bool) or not isinstance(self.epochs, int) or self.epochs <= 0:
            raise ValueError("Parser v5.1 training epochs must be a positive integer")
        if not math.isfinite(float(self.learning_rate)) or self.learning_rate <= 0:
            raise ValueError("Parser v5.1 training learning_rate must be positive and finite")
        if not math.isfinite(float(self.weight_decay)) or self.weight_decay < 0:
            raise ValueError("Parser v5.1 training weight_decay must be non-negative and finite")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("Parser v5.1 training seed must be an integer")
        if self.device not in {"cpu", "gpu"}:
            raise ValueError("Parser v5.1 training device must be cpu or gpu")
        self.model_config

    @property
    def model_config(self) -> ParserV51ModelConfig:
        return ParserV51ModelConfig(
            max_text_bytes=self.max_text_bytes,
            hidden_dim=self.hidden_dim,
            text_embedding_dim=self.text_embedding_dim,
            text_conv_dim=self.text_conv_dim,
            layers=self.layers,
            heads=self.heads,
            feedforward_multiplier=self.feedforward_multiplier,
            max_rows=self.max_rows,
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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
        raise ValueError(f"could not read Parser v5.1 training JSON {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Parser v5.1 training JSON must be an object: {path}")
    return value


def _datasets(manifests: Sequence[str | Path], *, label: str) -> list[ParserV5Dataset]:
    if not manifests:
        raise ValueError(f"Parser v5.1 training requires at least one {label} dataset")
    datasets = [load_parser_v5_dataset(path) for path in manifests]
    identities = [(dataset.dataset_id, dataset.samples_sha256) for dataset in datasets]
    if len(identities) != len(set(identities)):
        raise ValueError(f"Parser v5.1 {label} datasets must be unique")
    return datasets


def _implementation_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    identity: dict[str, str] = {}
    for name in _IMPLEMENTATION_FILES:
        source = root / name
        if not source.is_file():
            raise ValueError(f"Parser v5.1 training implementation source is missing: {name}")
        identity[name] = _sha256_file(source)
    return identity


def _profile(
    train: Sequence[ParserV5Dataset],
    validation: Sequence[ParserV5Dataset],
    config: ParserV51TrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_id": "parser_v51_direct_rows_v1",
        "train_datasets": [
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256}
            for dataset in train
        ],
        "validation_datasets": [
            {"dataset_id": dataset.dataset_id, "samples_sha256": dataset.samples_sha256}
            for dataset in validation
        ],
        "config": asdict(config),
        "implementation_sha256": _implementation_identity(),
    }


def _training_samples(datasets: Sequence[ParserV5Dataset]) -> list[Mapping[str, Any]]:
    return [sample for dataset in datasets for sample in dataset.samples]


def _train_epoch(
    model: ParserV51Model,
    optimizer: paddle.optimizer.Optimizer,
    datasets: Sequence[ParserV5Dataset],
    config: ParserV51TrainingConfig,
    *,
    epoch: int,
) -> tuple[float, int]:
    samples = _training_samples(datasets)
    rng = random.Random(config.seed + epoch * 1_000_003)
    rng.shuffle(samples)
    model.train()
    total_loss = 0.0
    steps = 0
    for sample in samples:
        tensors, targets, _ = prepare_parser_v51_sample(sample, config.model_config)
        output = model(tensors)
        loss = parser_v51_set_loss(output, targets)
        if not bool(paddle.isfinite(loss).item()):
            raise ValueError(f"non-finite Parser v5.1 training loss at epoch {epoch}")
        loss.backward()
        with paddle.utils.unique_name.guard():
            optimizer.step()
        optimizer.clear_grad()
        total_loss += float(loss.item())
        steps += 1
    if steps == 0:
        raise ValueError("Parser v5.1 training data produced no samples")
    return total_loss / steps, steps


@paddle.no_grad()
def evaluate_parser_v51(
    model: ParserV51Model,
    datasets: Sequence[ParserV5Dataset],
    config: ParserV51TrainingConfig,
) -> dict[str, float | int]:
    model.eval()
    total_loss = 0.0
    documents = 0
    target_rows = 0
    row_existence_correct = 0
    row_existence_total = 0
    field_presence_correct = 0
    field_presence_total = 0
    node_membership_correct = 0
    node_membership_total = 0
    node_membership_tp = 0
    node_membership_fp = 0
    node_membership_fn = 0
    span_exact = 0
    span_total = 0

    for sample in _training_samples(datasets):
        tensors, targets, _ = prepare_parser_v51_sample(sample, config.model_config)
        output = model(tensors)
        total_loss += float(parser_v51_set_loss(output, targets).item())
        documents += 1
        target_rows += len(targets.rows)
        assignments = match_parser_v51_rows(output, targets)
        matched = {row_index: target_index for row_index, target_index in assignments}
        existence_guess = (F.sigmoid(output.row_existence_logits) >= 0.5).astype("int64")
        for row_index in range(config.max_rows):
            expected = 1 if row_index in matched else 0
            row_existence_correct += int(int(existence_guess[row_index].item()) == expected)
            row_existence_total += 1

        membership_guess = F.sigmoid(output.field_node_logits) >= 0.5
        presence_guess = F.sigmoid(output.field_presence_logits) >= 0.5
        for row_index, target_index in assignments:
            row_target = targets.rows[target_index]
            for field_index, role in enumerate(ROW_FIELD_ROLES):
                field = row_target.field(role)
                required = required_field_pieces(field)
                expected_present = bool(required)
                field_presence_correct += int(bool(presence_guess[row_index, field_index].item()) == expected_present)
                field_presence_total += 1
                required_nodes = {piece.node_index for piece in required}
                for node_index in range(len(tensors.node_scalars)):
                    expected_member = node_index in required_nodes
                    guessed_member = bool(membership_guess[row_index, field_index, node_index].item())
                    node_membership_correct += int(
                        guessed_member == expected_member
                    )
                    node_membership_total += 1
                    node_membership_tp += int(guessed_member and expected_member)
                    node_membership_fp += int(guessed_member and not expected_member)
                    node_membership_fn += int(not guessed_member and expected_member)
                canonical = {}
                for piece in required:
                    canonical.setdefault(piece.node_index, piece)
                for node_index, piece in canonical.items():
                    start_guess = int(output.field_start_logits[row_index, field_index, node_index].argmax().item())
                    end_guess = int(output.field_end_logits[row_index, field_index, node_index].argmax().item())
                    span_exact += int(start_guess == piece.start_byte + 1 and end_guess == piece.end_byte)
                    span_total += 1

    if documents == 0:
        raise ValueError("Parser v5.1 validation data produced no samples")
    membership_precision = node_membership_tp / max(1, node_membership_tp + node_membership_fp)
    membership_recall = node_membership_tp / max(1, node_membership_tp + node_membership_fn)
    membership_f1 = (
        2.0 * membership_precision * membership_recall / (membership_precision + membership_recall)
        if membership_precision + membership_recall
        else 0.0
    )
    return {
        "documents": documents,
        "validation_loss": total_loss / documents,
        "target_rows": target_rows,
        "row_existence_accuracy": row_existence_correct / max(1, row_existence_total),
        "field_presence_accuracy": field_presence_correct / max(1, field_presence_total),
        "node_membership_accuracy": node_membership_correct / max(1, node_membership_total),
        "node_membership_precision": membership_precision,
        "node_membership_recall": membership_recall,
        "node_membership_f1": membership_f1,
        "span_exact_rate": span_exact / max(1, span_total),
        "span_supervised": span_total,
    }


def _checkpoint_directory(root: Path, epoch: int) -> Path:
    return root / CHECKPOINTS_DIR / f"epoch-{epoch:04d}"


def _save_checkpoint(
    root: Path,
    *,
    epoch: int,
    profile_sha256: str,
    model: ParserV51Model,
    optimizer: paddle.optimizer.Optimizer,
    training_loss: float,
    training_steps: int,
    validation: Mapping[str, float | int],
) -> dict[str, Any]:
    directory = _checkpoint_directory(root, epoch)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise ValueError(f"Parser v5.1 checkpoint {epoch} already exists")
    temporary = directory.parent / f".{directory.name}.tmp-{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        model_path = temporary / "model.pdparams"
        optimizer_path = temporary / "optimizer.pdopt"
        paddle.save(model.state_dict(), str(model_path))
        paddle.save(optimizer.state_dict(), str(optimizer_path))
        record = {
            "epoch": epoch,
            "profile_sha256": profile_sha256,
            "training_loss": training_loss,
            "training_steps": training_steps,
            "validation": dict(validation),
            "checkpoint": str((_checkpoint_directory(root, epoch) / "model.pdparams").relative_to(root)),
            "model_sha256": _sha256_file(model_path),
            "optimizer_sha256": _sha256_file(optimizer_path),
        }
        _atomic_json(temporary / "checkpoint.json", record)
        os.replace(temporary, directory)
        return record
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _load_checkpoint(
    root: Path,
    history: Sequence[Mapping[str, Any]],
    model: ParserV51Model,
    optimizer: paddle.optimizer.Optimizer,
) -> None:
    if not history:
        return
    epoch = int(history[-1]["epoch"])
    directory = _checkpoint_directory(root, epoch)
    model.set_state_dict(paddle.load(str(directory / "model.pdparams")))
    optimizer.set_state_dict(paddle.load(str(directory / "optimizer.pdopt")))


def _validated_history(
    root: Path,
    state: Mapping[str, Any],
    *,
    profile_sha256: str,
    max_epoch: int,
) -> list[dict[str, Any]]:
    raw_history = state.get("history")
    if not isinstance(raw_history, list):
        raise ValueError("Parser v5.1 training history is invalid")
    history: list[dict[str, Any]] = []
    for expected_epoch, raw in enumerate(raw_history, start=1):
        if not isinstance(raw, Mapping) or raw.get("epoch") != expected_epoch or expected_epoch > max_epoch:
            raise ValueError("Parser v5.1 training history epochs are invalid")
        directory = _checkpoint_directory(root, expected_epoch)
        model_path = directory / "model.pdparams"
        optimizer_path = directory / "optimizer.pdopt"
        if raw.get("profile_sha256") != profile_sha256:
            raise ValueError("Parser v5.1 checkpoint profile mismatch")
        if raw.get("model_sha256") != _sha256_file(model_path):
            raise ValueError("Parser v5.1 checkpoint model SHA-256 mismatch")
        if raw.get("optimizer_sha256") != _sha256_file(optimizer_path):
            raise ValueError("Parser v5.1 checkpoint optimizer SHA-256 mismatch")
        history.append(dict(raw))
    return history


def train_parser_v51(
    *,
    train_manifests: Sequence[str | Path],
    validation_manifests: Sequence[str | Path],
    output_dir: str | Path,
    config: ParserV51TrainingConfig = ParserV51TrainingConfig(),
) -> dict[str, Any]:
    train = _datasets(train_manifests, label="train")
    validation = _datasets(validation_manifests, label="validation")
    train_hashes = {dataset.samples_sha256 for dataset in train}
    if any(dataset.samples_sha256 in train_hashes for dataset in validation):
        raise ValueError("Parser v5.1 validation dataset must be disjoint from training data")
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
                raise ValueError("Parser v5.1 training profile does not match existing output")
            if state.get("status") == "completed":
                result_path = root / RESULT_FILE
                if state.get("result_sha256") != _sha256_file(result_path):
                    raise ValueError("completed Parser v5.1 training result SHA-256 mismatch")
                result = _json_file(result_path)
                if result.get("profile") != profile or result.get("status") != "ok":
                    raise ValueError("completed Parser v5.1 training result profile mismatch")
                return result
            if state.get("status") != "running":
                raise ValueError("Parser v5.1 training state status is invalid")
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
            model = ParserV51Model(config.model_config)
            optimizer = paddle.optimizer.AdamW(
                learning_rate=config.learning_rate,
                parameters=model.parameters(),
                weight_decay=config.weight_decay,
            )
        history = _validated_history(root, state, profile_sha256=profile_sha256, max_epoch=config.epochs)
        _load_checkpoint(root, history, model, optimizer)

        for epoch in range(len(history) + 1, config.epochs + 1):
            training_loss, steps = _train_epoch(model, optimizer, train, config, epoch=epoch)
            validation_metrics = evaluate_parser_v51(model, validation, config)
            record = _save_checkpoint(
                root,
                epoch=epoch,
                profile_sha256=profile_sha256,
                model=model,
                optimizer=optimizer,
                training_loss=training_loss,
                training_steps=steps,
                validation=validation_metrics,
            )
            history.append(record)
            state["completed_epoch"] = epoch
            state["history"] = history
            _atomic_json(state_path, state)
            print(
                f"[ocr-parser-v51-train] {epoch}/{config.epochs} "
                f"loss={training_loss:.5f} val={float(validation_metrics['validation_loss']):.5f}",
                flush=True,
            )

        best = min(history, key=lambda item: (float(item["validation"]["validation_loss"]), int(item["epoch"])))
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


__all__ = ["ParserV51TrainingConfig", "evaluate_parser_v51", "train_parser_v51"]