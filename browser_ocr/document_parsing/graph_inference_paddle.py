from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import paddle

from .document_graph import (
    EDGE_FEATURE_DIM,
    PAGE_NODE_ID,
    ROLE_LABELS,
    DocumentGraph,
    GraphEncoderSpec,
    build_document_graph,
    relation_pair_features,
)
from .graph_decode import DecodeConfig, decode_candidates, decode_graph_scores
from .graph_encoder_paddle import SparseDocumentGraphEncoder, architecture_manifest, graph_tensors, model_parameter_count


@dataclass(frozen=True)
class GraphModelBundle:
    result_path: Path
    checkpoint: Path
    checkpoint_sha256: str
    spec: GraphEncoderSpec
    profile: Mapping[str, Any]
    model: SparseDocumentGraphEncoder


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read parser graph model result {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("parser graph model result must be a JSON object")
    return value


def _spec_from_architecture(raw: object) -> GraphEncoderSpec:
    if not isinstance(raw, Mapping):
        raise ValueError("parser graph model result is missing architecture")
    try:
        spec = GraphEncoderSpec(
            hidden_dim=int(raw["hidden_dim"]),
            layers=int(raw["layers"]),
            neighbor_count=int(raw["neighbor_count"]),
            pair_hidden_dim=int(raw["pair_hidden_dim"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("parser graph model architecture is invalid") from exc
    if dict(raw) != architecture_manifest(spec):
        raise ValueError("parser graph model architecture does not match the supported graph contract")
    return spec


def load_graph_model(result_path: str | Path, *, device: str = "cpu") -> GraphModelBundle:
    path = Path(result_path).resolve()
    result = _json_object(path)
    state_path = path.parent / "training-state.json"
    state = _json_object(state_path)
    if state.get("status") != "completed":
        raise ValueError("parser graph training state is not completed")
    expected_result_sha = str(state.get("result_sha256") or "")
    if expected_result_sha != _sha256_file(path):
        raise ValueError("parser graph training state/result SHA-256 mismatch")
    if state.get("profile") != result.get("profile"):
        raise ValueError("parser graph training state/result profile mismatch")
    if result.get("status") != "ok":
        raise ValueError("parser graph model result is not successful")
    profile = result.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("parser graph model result profile must be an object")
    spec = _spec_from_architecture(profile.get("architecture"))
    checkpoint_raw = result.get("best_checkpoint")
    if not isinstance(checkpoint_raw, str) or not checkpoint_raw:
        raise ValueError("parser graph model result is missing best_checkpoint")
    checkpoint = Path(checkpoint_raw)
    if not checkpoint.is_absolute():
        checkpoint = path.parent / checkpoint
    checkpoint = checkpoint.resolve()
    expected_sha = str(result.get("best_checkpoint_sha256") or "")
    if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise ValueError("parser graph model result is missing a valid checkpoint SHA-256")
    if not checkpoint.is_file() or _sha256_file(checkpoint) != expected_sha:
        raise ValueError("parser graph model checkpoint SHA-256 mismatch")
    if device not in {"cpu", "gpu"}:
        raise ValueError("parser graph inference device must be cpu or gpu")
    paddle.set_device(device)
    with paddle.utils.unique_name.guard():
        model = SparseDocumentGraphEncoder(spec)
    try:
        model.set_state_dict(paddle.load(str(checkpoint)))
    except Exception as exc:
        raise ValueError("parser graph model checkpoint cannot be loaded") from exc
    expected_parameters = result.get("parameter_count")
    if isinstance(expected_parameters, bool) or not isinstance(expected_parameters, int):
        raise ValueError("parser graph model result is missing parameter_count")
    if model_parameter_count(model) != expected_parameters:
        raise ValueError("parser graph model parameter count disagrees with result metadata")
    model.eval()
    return GraphModelBundle(
        result_path=path,
        checkpoint=checkpoint,
        checkpoint_sha256=expected_sha,
        spec=spec,
        profile=dict(profile),
        model=model,
    )


def build_inference_graph(document: Mapping[str, Any], *, neighbor_count: int) -> DocumentGraph:
    observation = document.get("observation")
    if not isinstance(observation, Mapping) or not isinstance(observation.get("nodes"), list):
        raise ValueError("parser document observation nodes are required")
    nodes: list[dict[str, object]] = []
    for raw in observation["nodes"]:
        if not isinstance(raw, Mapping):
            raise ValueError("parser document observation contains a non-object node")
        nodes.append({
            "node_id": raw.get("node_id"),
            "text": raw.get("text"),
            "confidence": raw.get("confidence"),
            "polygon": raw.get("polygon"),
            "label_status": "unlabeled",
            "semantic_role": None,
            "association_group": None,
        })
    # Deliberately reconstruct only the inference-visible projection. Gold target
    # region ids, semantic labels, association groups and relation supervision
    # cannot influence graph features even if the caller passes a labeled dataset.
    inference_document = {
        "document_id": document.get("document_id"),
        "width": document.get("width"),
        "height": document.get("height"),
        "observation": {"nodes": nodes},
        "relations": [],
    }
    return build_document_graph(inference_document, neighbor_count=neighbor_count)


@paddle.no_grad()
def score_graph_document(
    bundle: GraphModelBundle,
    document: Mapping[str, Any],
) -> tuple[DocumentGraph, dict[str, dict[str, float]], paddle.Tensor]:
    graph = build_inference_graph(document, neighbor_count=bundle.spec.neighbor_count)
    tensors = graph_tensors(graph)
    hidden = bundle.model.encode(tensors.node_features, tensors.edge_index, tensors.edge_features)
    probabilities = paddle.nn.functional.softmax(bundle.model.role_head(hidden), axis=1).numpy()
    role_scores: dict[str, dict[str, float]] = {}
    for index, node in enumerate(graph.nodes):
        if node.node_id == PAGE_NODE_ID:
            continue
        role_scores[node.node_id] = {
            role: float(probabilities[index, role_index])
            for role_index, role in enumerate(ROLE_LABELS)
        }
    return graph, role_scores, hidden


@paddle.no_grad()
def score_graph_relations(
    bundle: GraphModelBundle,
    graph: DocumentGraph,
    hidden: paddle.Tensor,
    pairs: Sequence[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    if not pairs:
        return {}
    seen: set[tuple[str, str]] = set()
    relation_index: list[list[int]] = []
    relation_features: list[list[float]] = []
    ordered_pairs: list[tuple[str, str]] = []
    for product_id, field_id in pairs:
        pair = (str(product_id), str(field_id))
        if pair in seen:
            raise ValueError("parser graph relation score pairs must be unique")
        seen.add(pair)
        if pair[0] not in graph.node_index or pair[1] not in graph.node_index:
            raise ValueError("parser graph relation score pair references an unknown OCR node")
        product_index = int(graph.node_index[pair[0]])
        field_index = int(graph.node_index[pair[1]])
        relation_index.append([product_index, field_index])
        relation_features.append(list(relation_pair_features(graph, product_index, field_index)))
        ordered_pairs.append(pair)
    indices = paddle.to_tensor(relation_index, dtype="int64").reshape([-1, 2])
    features = paddle.to_tensor(relation_features, dtype="float32").reshape([-1, EDGE_FEATURE_DIM])
    logits = bundle.model.relation_logits(hidden, indices, features)
    probabilities = paddle.nn.functional.sigmoid(logits).numpy().tolist()
    return {pair: float(score) for pair, score in zip(ordered_pairs, probabilities, strict=True)}


def infer_graph_document(
    bundle: GraphModelBundle,
    document: Mapping[str, Any],
    *,
    config: DecodeConfig = DecodeConfig(),
) -> list[dict[str, Any]]:
    graph, role_scores, hidden = score_graph_document(bundle, document)
    products, fields = decode_candidates(document, role_scores, config=config)
    pairs = [(product_id, field_id) for product_id in products for field_id, _ in fields]
    association_scores = score_graph_relations(bundle, graph, hidden, pairs)
    return decode_graph_scores(document, role_scores, association_scores, config=config)


__all__ = [
    "GraphModelBundle",
    "build_inference_graph",
    "infer_graph_document",
    "load_graph_model",
    "score_graph_document",
    "score_graph_relations",
]