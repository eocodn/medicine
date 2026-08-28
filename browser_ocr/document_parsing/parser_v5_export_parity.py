from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import onnxruntime as ort
import paddle

from .parser_v5_encoder_paddle import parser_v5_tensors
from .parser_v5_heads_paddle import FIELD_ROLE_LABELS
from .parser_v5_model_input import build_parser_v5_runtime_input
from .parser_v5_observation import ObservationProfile, simulate_observations
from .parser_v5_training_artifact import resolve_parser_v5_checkpoint
from .parser_v5_training_paddle import ParserV5Model, ParserV5TrainingConfig
from .parser_v5_world import ParserWorldProfile, generate_parser_world


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def load_frozen_parser_v5_export_model(
    training_result: Path,
    freeze: Mapping[str, Any],
) -> tuple[ParserV5Model, ParserV5TrainingConfig]:
    result = _json_object(training_result, label="Parser v5 training result")
    if _sha256_file(training_result) != freeze["training_result_sha256"]:
        raise ValueError("Parser v5 export training result disagrees with candidate freeze")
    profile = result.get("profile")
    if not isinstance(profile, Mapping) or result.get("profile_sha256") != freeze["training_profile_sha256"]:
        raise ValueError("Parser v5 export training profile disagrees with candidate freeze")
    config_raw = profile.get("config")
    if not isinstance(config_raw, Mapping):
        raise ValueError("Parser v5 export training config is invalid")
    training_config = ParserV5TrainingConfig(**dict(config_raw))
    config = replace(training_config, device="cpu")
    checkpoint = resolve_parser_v5_checkpoint(training_result, result.get("best_checkpoint"))
    if not checkpoint.is_file() or _sha256_file(checkpoint) != freeze["checkpoint_sha256"]:
        raise ValueError("Parser v5 export checkpoint disagrees with candidate freeze")

    # Export parity uses the same deterministic CPU execution class as the
    # ONNX Runtime reference. The persisted training device is not an
    # architectural parameter: trained CPU/GPU GEMM kernels may differ by a
    # few 1e-3 in logits even when CPU Paddle and CPU ONNX agree to <2e-5.
    paddle.set_device("cpu")
    with paddle.utils.unique_name.guard():
        model = ParserV5Model(config)
    model.set_state_dict(paddle.load(str(checkpoint)))
    model.eval()
    return model, config


def run_parser_v5_export_parity_case(
    model: ParserV5Model,
    config: ParserV5TrainingConfig,
    *,
    seed: int,
    session: ort.InferenceSession,
    empty_assignments: bool = False,
) -> dict[str, Any]:
    truth = generate_parser_world(
        seed=seed,
        document_index=0,
        profile=ParserWorldProfile(medication_count=(2, 2), distractor_section_count=(1, 2)),
    )
    observation = simulate_observations(
        truth,
        seed=seed + 1,
        profile=ObservationProfile(
            text_corruption_rate=0.05,
            drop_rate=0,
            duplicate_rate=0,
            split_rate=0,
            merge_rate=0,
            geometry_jitter=0.002,
            false_positive_count=(1, 1),
            reading_order_shuffle_rate=0,
        ),
    )
    nodes = [
        {
            "node_id": str(node["node_id"]),
            "text": str(node["text"]),
            "detector_confidence": float(node["detector_confidence"]),
            "recognizer_confidence": float(node["recognizer_confidence"]),
            "polygon": node["polygon"],
        }
        for node in observation["nodes"]
    ]
    value = build_parser_v5_runtime_input(
        document_id=str(truth["document_id"]),
        width=truth["width"],
        height=truth["height"],
        nodes=nodes,
        max_text_bytes=config.max_text_bytes,
    )
    tensors = parser_v5_tensors(value)
    node_count = len(nodes)
    product_indices = [] if empty_assignments else list(range(min(2, node_count)))
    field_indices = [] if empty_assignments else list(range(min(2, node_count), min(5, node_count)))
    membership = np.zeros((len(product_indices), node_count), dtype=np.float32)
    for slot, index in enumerate(product_indices):
        membership[slot, index] = 1.0
    field_role_index = np.asarray(
        [index % len(FIELD_ROLE_LABELS) for index in range(len(field_indices))],
        dtype=np.int64,
    )
    product_available = np.ones((len(product_indices),), dtype=np.bool_)
    field_node_index = np.asarray(field_indices, dtype=np.int64)

    with paddle.no_grad():
        hidden, role_logits = model.encoder(tensors)
        candidate_logits = model.heads.candidate_head(hidden).reshape([-1])
        assignment_logits = model.heads.score_assignments(
            hidden,
            tensors.relation_features,
            product_membership=paddle.to_tensor(membership, dtype="float32"),
            product_available=paddle.to_tensor(product_available, dtype="bool"),
            field_node_index=paddle.to_tensor(field_node_index, dtype="int64"),
            field_role_index=paddle.to_tensor(field_role_index, dtype="int64"),
        )
    feeds = {
        "token_ids": tensors.token_ids.numpy(),
        "token_mask": tensors.token_mask.numpy(),
        "node_scalars": tensors.node_scalars.numpy(),
        "relation_features": tensors.relation_features.numpy(),
        "product_membership": membership,
        "product_available": product_available,
        "field_node_index": field_node_index,
        "field_role_index": field_role_index,
    }
    onnx_role, onnx_candidate, onnx_assignment = session.run(None, feeds)
    deltas = [
        float(np.max(np.abs(onnx_role - role_logits.numpy()))) if onnx_role.size else 0.0,
        float(np.max(np.abs(onnx_candidate - candidate_logits.numpy()))) if onnx_candidate.size else 0.0,
        float(np.max(np.abs(onnx_assignment - assignment_logits.numpy()))) if onnx_assignment.size else 0.0,
    ]
    return {
        "seed": seed,
        "nodes": node_count,
        "products": len(product_indices),
        "fields": len(field_indices),
        "max_abs_delta": max(deltas),
    }


__all__ = ["load_frozen_parser_v5_export_model", "run_parser_v5_export_parity_case"]