from __future__ import annotations

import fcntl
import hashlib
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import paddle

from .document_graph import GraphEncoderSpec, ROLE_LABELS, build_document_graph
from .graph_encoder_paddle import (
    SparseDocumentGraphEncoder,
    architecture_manifest,
    graph_loss,
    graph_tensors,
    model_parameter_count,
)
from .graph_training_io import (
    LOCK_FILE,
    RESULT_FILE,
    STATE_FILE,
    adopt_checkpoints,
    atomic_checkpoint,
    atomic_json,
    canonical_json,
    json_file,
    load_latest_checkpoint,
    sha256_bytes,
    sha256_file,
    validate_completed_result,
)
from .training_dataset import ParserDataset, load_parser_dataset


@dataclass(frozen=True)
class GraphTrainingConfig:
    epochs: int = 12
    learning_rate: float = 0.003
    weight_decay: float = 1e-4
    seed: int = 112
    hidden_dim: int = 96
    layers: int = 2
    neighbor_count: int = 12
    pair_hidden_dim: int = 64
    relation_loss_weight: float = 1.0
    max_relation_pos_weight: float = 8.0
    device: str = "gpu"

    def __post_init__(self) -> None:
        if isinstance(self.epochs, bool) or not isinstance(self.epochs, int) or self.epochs <= 0:
            raise ValueError("epochs must be a positive integer")
        if not math.isfinite(float(self.learning_rate)) or self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive and finite")
        if not math.isfinite(float(self.weight_decay)) or self.weight_decay < 0:
            raise ValueError("weight_decay must be non-negative and finite")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if not math.isfinite(float(self.relation_loss_weight)) or self.relation_loss_weight <= 0:
            raise ValueError("relation_loss_weight must be positive and finite")
        if not math.isfinite(float(self.max_relation_pos_weight)) or self.max_relation_pos_weight < 1:
            raise ValueError("max_relation_pos_weight must be finite and at least 1")
        if self.device not in {"cpu", "gpu"}:
            raise ValueError("device must be cpu or gpu")
        GraphEncoderSpec(
            hidden_dim=self.hidden_dim,
            layers=self.layers,
            neighbor_count=self.neighbor_count,
            pair_hidden_dim=self.pair_hidden_dim,
        )

    @property
    def spec(self) -> GraphEncoderSpec:
        return GraphEncoderSpec(
            hidden_dim=self.hidden_dim,
            layers=self.layers,
            neighbor_count=self.neighbor_count,
            pair_hidden_dim=self.pair_hidden_dim,
        )


def _implementation_sha256() -> str:
    root = Path(__file__).resolve().parent
    sources = [
        root / "document_graph.py",
        root / "graph_encoder_paddle.py",
        root / "graph_training_io.py",
        Path(__file__).resolve(),
    ]
    digest = hashlib.sha256()
    for source in sources:
        relative = source.name.encode("utf-8")
        content = source.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _dataset_identity(dataset: ParserDataset) -> dict[str, str]:
    return {
        "dataset_id": dataset.dataset_id,
        "fingerprint": dataset.fingerprint,
    }


def _load_training_datasets(
    train_manifests: Sequence[str | Path],
    val_manifests: Sequence[str | Path],
) -> tuple[list[ParserDataset], list[ParserDataset]]:
    if not train_manifests or not val_manifests:
        raise ValueError("graph training requires at least one train and one validation dataset")
    train = [load_parser_dataset(path) for path in train_manifests]
    validation = [load_parser_dataset(path) for path in val_manifests]
    train_splits = {document["split"] for dataset in train for document in dataset.documents}
    val_splits = {document["split"] for dataset in validation for document in dataset.documents}
    if train_splits != {"train"}:
        raise ValueError("graph training datasets must contain train documents only")
    if val_splits != {"val"}:
        raise ValueError("graph validation datasets must contain val documents only")
    return train, validation


def _graphs(datasets: Sequence[ParserDataset], spec: GraphEncoderSpec):
    return [
        build_document_graph(document, neighbor_count=spec.neighbor_count)
        for dataset in datasets
        for document in dataset.documents
    ]


def _role_weights(graphs) -> paddle.Tensor:
    counts = [0] * len(ROLE_LABELS)
    for graph in graphs:
        for node in graph.nodes:
            if node.supervised and node.role_target is not None:
                counts[node.role_target] += 1
    nonzero = [count for count in counts if count]
    if not nonzero:
        raise ValueError("graph training data has no supervised role targets")
    largest = max(nonzero)
    values = [min(4.0, math.sqrt(largest / count)) if count else 0.0 for count in counts]
    return paddle.to_tensor(values, dtype="float32")


def _relation_pos_weight(graphs, maximum: float) -> float:
    positive = sum(relation.label == 1 for graph in graphs for relation in graph.relations)
    negative = sum(relation.label == 0 for graph in graphs for relation in graph.relations)
    if positive == 0:
        raise ValueError("graph training data has no positive medication associations")
    if negative == 0:
        return 1.0
    return min(float(maximum), max(1.0, negative / positive))


def _train_epoch(
    model: SparseDocumentGraphEncoder,
    optimizer: paddle.optimizer.Optimizer,
    graphs,
    *,
    epoch: int,
    seed: int,
    role_weight: paddle.Tensor,
    relation_loss_weight: float,
    relation_pos_weight: float,
) -> float:
    order = list(range(len(graphs)))
    random.Random(seed + epoch * 1_000_003).shuffle(order)
    model.train()
    total_loss = 0.0
    for graph_index in order:
        tensors = graph_tensors(graphs[graph_index])
        role_logits, relation_logits = model(
            tensors.node_features,
            tensors.edge_index,
            tensors.edge_features,
            tensors.relation_index,
            tensors.relation_features,
        )
        loss = graph_loss(
            role_logits,
            relation_logits,
            tensors,
            role_weight=role_weight,
            relation_loss_weight=relation_loss_weight,
            relation_pos_weight=relation_pos_weight,
        )
        if not bool(paddle.isfinite(loss).item()):
            raise ValueError(f"non-finite graph training loss at epoch {epoch}")
        loss.backward()
        # AdamW creates accumulator tensors lazily on its first step and uses
        # Paddle's global unique-name counter for those tensor names. Keep that
        # first creation deterministic too, otherwise an in-process resume can
        # have stable model parameter names but incompatible optimizer keys.
        with paddle.utils.unique_name.guard():
            optimizer.step()
        optimizer.clear_grad()
        total_loss += float(loss.item())
    return total_loss / len(order)


def _f_score(precision: float, recall: float, beta: float) -> float:
    beta_squared = beta * beta
    denominator = beta_squared * precision + recall
    if denominator <= 0:
        return 0.0
    return (1.0 + beta_squared) * precision * recall / denominator


@paddle.no_grad()
def evaluate_graph_model(model: SparseDocumentGraphEncoder, graphs) -> dict[str, Any]:
    model.eval()
    confusion = [[0] * len(ROLE_LABELS) for _ in ROLE_LABELS]
    relation_tp = relation_fp = relation_fn = relation_tn = 0
    role_total = role_correct = 0
    for graph in graphs:
        tensors = graph_tensors(graph)
        role_logits, relation_logits = model(
            tensors.node_features,
            tensors.edge_index,
            tensors.edge_features,
            tensors.relation_index,
            tensors.relation_features,
        )
        if tensors.role_index.shape[0] > 0:
            supervised = paddle.gather(role_logits, tensors.role_index, axis=0)
            predicted = paddle.argmax(supervised, axis=1).numpy().tolist()
            targets = tensors.role_targets.numpy().tolist()
            for actual, guess in zip(targets, predicted, strict=True):
                actual_index = int(actual)
                guess_index = int(guess)
                confusion[actual_index][guess_index] += 1
                role_total += 1
                role_correct += int(actual_index == guess_index)
        if tensors.relation_labels.shape[0] > 0:
            predicted_relations = (paddle.nn.functional.sigmoid(relation_logits) >= 0.5).numpy().tolist()
            targets = tensors.relation_labels.numpy().tolist()
            for actual, guess in zip(targets, predicted_relations, strict=True):
                positive = float(actual) >= 0.5
                predicted_positive = bool(guess)
                if positive and predicted_positive:
                    relation_tp += 1
                elif positive:
                    relation_fn += 1
                elif predicted_positive:
                    relation_fp += 1
                else:
                    relation_tn += 1

    role_f1: dict[str, float] = {}
    supported_f1: list[float] = []
    for role_index, role in enumerate(ROLE_LABELS):
        tp = confusion[role_index][role_index]
        fp = sum(confusion[actual][role_index] for actual in range(len(ROLE_LABELS)) if actual != role_index)
        fn = sum(confusion[role_index][guess] for guess in range(len(ROLE_LABELS)) if guess != role_index)
        support = sum(confusion[role_index])
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = _f_score(precision, recall, 1.0)
        role_f1[role] = f1
        if support:
            supported_f1.append(f1)
    role_macro_f1 = sum(supported_f1) / len(supported_f1) if supported_f1 else 0.0
    relation_precision = relation_tp / (relation_tp + relation_fp) if relation_tp + relation_fp else 0.0
    relation_recall = relation_tp / (relation_tp + relation_fn) if relation_tp + relation_fn else 0.0
    relation_f0_5 = _f_score(relation_precision, relation_recall, 0.5)
    relation_f1 = _f_score(relation_precision, relation_recall, 1.0)
    if relation_tp + relation_fn == 0:
        raise ValueError("graph validation data has no positive medication associations")
    selection_score = 0.65 * relation_f0_5 + 0.35 * role_macro_f1
    return {
        "role_accuracy": role_correct / role_total if role_total else 0.0,
        "role_macro_f1": role_macro_f1,
        "role_f1": role_f1,
        "relation_precision": relation_precision,
        "relation_recall": relation_recall,
        "relation_f0_5": relation_f0_5,
        "relation_f1": relation_f1,
        "relation_tp": relation_tp,
        "relation_fp": relation_fp,
        "relation_fn": relation_fn,
        "relation_tn": relation_tn,
        "selection_score": selection_score,
    }


def _selection_key(record: Mapping[str, Any]) -> tuple[float, float, float, float, int]:
    metrics = record["validation"]
    return (
        float(metrics["selection_score"]),
        float(metrics["relation_precision"]),
        float(metrics["relation_recall"]),
        float(metrics["role_macro_f1"]),
        -int(record["epoch"]),
    )


def _profile(
    train: Sequence[ParserDataset],
    validation: Sequence[ParserDataset],
    config: GraphTrainingConfig,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "implementation_sha256": _implementation_sha256(),
        "train_datasets": [_dataset_identity(dataset) for dataset in train],
        "validation_datasets": [_dataset_identity(dataset) for dataset in validation],
        "config": asdict(config),
        "architecture": architecture_manifest(config.spec),
    }


def run_graph_training(
    *,
    train_manifests: Sequence[str | Path],
    val_manifests: Sequence[str | Path],
    run_dir: str | Path,
    config: GraphTrainingConfig = GraphTrainingConfig(),
) -> dict[str, Any]:
    train_datasets, val_datasets = _load_training_datasets(train_manifests, val_manifests)
    profile = _profile(train_datasets, val_datasets, config)
    profile_sha256 = sha256_bytes(canonical_json(profile))
    root = Path(run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_FILE
    state_path = root / STATE_FILE
    result_path = root / RESULT_FILE
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"graph training is already active in {root}") from exc

        if state_path.is_file():
            state = json_file(state_path)
            if state.get("profile") != profile:
                raise ValueError("graph training profile differs from existing state")
            if state.get("status") == "completed":
                return validate_completed_result(root, profile, state)
            if state.get("status") not in {"running", "failed"}:
                raise ValueError("graph training state has unsupported status")
        else:
            unexpected = [path.name for path in root.iterdir() if path.name != LOCK_FILE]
            if unexpected:
                raise ValueError("graph training output is non-empty without authoritative state")
            state = {
                "schema_version": 1,
                "status": "running",
                "profile": profile,
                "profile_sha256": profile_sha256,
                "completed_epoch": 0,
                "history": [],
            }
            atomic_json(state_path, state)

        if state.get("profile_sha256") != profile_sha256:
            raise ValueError("graph training state profile SHA-256 mismatch")
        history = adopt_checkpoints(root, state, profile_sha256, config.epochs)
        state.update(status="running", history=history, completed_epoch=len(history))
        state.pop("last_error", None)
        atomic_json(state_path, state)

        paddle.set_device(config.device)
        paddle.seed(config.seed)
        spec = config.spec
        train_graphs = _graphs(train_datasets, spec)
        val_graphs = _graphs(val_datasets, spec)
        role_weight = _role_weights(train_graphs)
        relation_pos_weight = _relation_pos_weight(train_graphs, config.max_relation_pos_weight)
        # Paddle optimizer checkpoints key accumulator tensors by the underlying
        # parameter names. A fresh model created later in the same Python process
        # would otherwise receive incremented global names (linear_17, ...), which
        # makes a valid optimizer state impossible to restore. The local naming
        # guard gives this standalone model deterministic parameter names without
        # changing process-global naming outside the trainer.
        with paddle.utils.unique_name.guard():
            model = SparseDocumentGraphEncoder(spec)
        if model_parameter_count(model) >= 1_000_000:
            raise ValueError("graph parser exceeds the initial one-million-parameter mobile budget")
        optimizer = paddle.optimizer.AdamW(
            learning_rate=config.learning_rate,
            weight_decay=config.weight_decay,
            parameters=model.parameters(),
        )
        load_latest_checkpoint(root, history, model, optimizer)

        try:
            for epoch in range(len(history) + 1, config.epochs + 1):
                training_loss = _train_epoch(
                    model,
                    optimizer,
                    train_graphs,
                    epoch=epoch,
                    seed=config.seed,
                    role_weight=role_weight,
                    relation_loss_weight=config.relation_loss_weight,
                    relation_pos_weight=relation_pos_weight,
                )
                validation = evaluate_graph_model(model, val_graphs)
                record = atomic_checkpoint(
                    root,
                    epoch=epoch,
                    profile_sha256=profile_sha256,
                    model=model,
                    optimizer=optimizer,
                    training_loss=training_loss,
                    validation=validation,
                )
                history.append(record)
                state.update(status="running", completed_epoch=epoch, history=history)
                atomic_json(state_path, state)
                print(
                    f"[ocr-parser-train] {epoch}/{config.epochs} "
                    f"loss={training_loss:.5f} selection={validation['selection_score']:.5f}",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as exc:
            state.update(
                status="failed",
                completed_epoch=len(history),
                history=history,
                last_error=str(exc)[:1000],
            )
            atomic_json(state_path, state)
            raise

        best = max(history, key=_selection_key)
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "epochs_completed": len(history),
            "best_epoch": best["epoch"],
            "best_checkpoint": best["checkpoint"],
            "best_checkpoint_sha256": best["model_sha256"],
            "best_validation": best["validation"],
            "history": history,
            "parameter_count": model_parameter_count(model),
            "relation_pos_weight": relation_pos_weight,
        }
        atomic_json(result_path, result)
        state.update(
            status="completed",
            completed_epoch=len(history),
            history=history,
            result_sha256=sha256_file(result_path),
        )
        state.pop("last_error", None)
        atomic_json(state_path, state)
        return result


__all__ = [
    "GraphTrainingConfig",
    "evaluate_graph_model",
    "run_graph_training",
]