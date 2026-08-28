from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import onnx
import onnxruntime as ort
import paddle
from onnx import TensorProto, helper

from .artifact_storage import atomic_write, exclusive_output_lock
from .parser_v5_document_encoder_paddle import parser_v5_document_tensors
from .parser_v5_export_onnx import (
    ONNX_IR_VERSION,
    OPSET_VERSION,
    _build_onnx_model as _build_v5_onnx_model,
    _gelu,
    _layer_norm,
    _linear,
    _squeeze,
    _state_initializers,
    _tensor,
    _unsqueeze,
)
from .parser_v5_model_input import BYTE_OFFSET, NODE_SCALAR_DIM, RELATION_FEATURE_DIM, build_parser_v5_runtime_document_input
from .parser_v5_observation import ObservationProfile, simulate_observations
from .parser_v5_training_paddle import ParserV5Model, ParserV5TrainingConfig
from .parser_v5_world import ParserWorldProfile, generate_parser_world
from .parser_v51_inference_paddle import load_trained_parser_v51_model, runtime_memory_from_paddle
from .parser_v51_runtime_decode import ParserV51RuntimeMemory, decode_parser_v51_memory
from .parser_v51_targets import ROW_FIELD_ROLES
from .parser_v51_validation_protocol import load_parser_v51_candidate_freeze, validate_parser_v51_frozen_implementation

MODEL_FILE = "parser-v51-memory.onnx"
MANIFEST_FILE = "manifest.json"
PARITY_TOLERANCE = 2e-5
_EXPORT_FILES = ("parser_v51_export_onnx.py",)
_OUTPUT_NAMES = (
    "row_existence_logits",
    "field_query_states",
    "node_pointer_keys",
    "start_pointer_keys",
    "end_pointer_keys",
    "evidence_values",
    "token_valid_mask",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {name: _sha256_file(root / name) for name in _EXPORT_FILES}


def _topology_config(config) -> ParserV5TrainingConfig:
    return ParserV5TrainingConfig(
        epochs=1,
        learning_rate=0.001,
        weight_decay=0.0,
        seed=1,
        max_text_bytes=config.max_text_bytes,
        hidden_dim=config.hidden_dim,
        text_embedding_dim=config.text_embedding_dim,
        text_conv_dim=config.text_conv_dim,
        layers=config.layers,
        heads=config.heads,
        feedforward_multiplier=config.feedforward_multiplier,
        device="cpu",
    )


def _encoder_prefix(config) -> tuple[list[onnx.NodeProto], list[onnx.TensorProto], str, str]:
    topology_config = _topology_config(config)
    topology = ParserV5Model(topology_config)
    base = _build_v5_onnx_model(topology, topology_config)
    nodes: list[onnx.NodeProto] = []
    for node in base.graph.node:
        if any(name == "encoder.role_head.weight" for name in node.input):
            break
        nodes.append(node)
    constants = [initializer for initializer in base.graph.initializer if initializer.name.startswith("constant.")]
    hidden = f"layer{config.layers - 1}.norm2"
    return nodes, constants, hidden, "text.encoded"


def _append_decoder(
    *,
    nodes: list[onnx.NodeProto],
    initializers: list[onnx.TensorProto],
    hidden: str,
    encoded: str,
    config,
) -> None:
    h = int(config.hidden_dim)
    scale_name = "v51.constant.sqrt_hidden"
    byte_offset_name = "v51.constant.byte_offset"
    initializers.append(_tensor(scale_name, np.asarray(math.sqrt(h), dtype=np.float32)))
    initializers.append(_tensor(byte_offset_name, np.asarray(BYTE_OFFSET, dtype=np.int64)))

    token_states = "v51.token_states"
    nodes.append(helper.make_node("Transpose", [encoded], [token_states], perm=[0, 2, 1], name="V51.TokenStates"))

    q = _linear(nodes, "decoder.row_queries", "decoder.row_self_query", "v51.self.q")
    k = _linear(nodes, "decoder.row_queries", "decoder.row_self_key", "v51.self.k")
    v = _linear(nodes, "decoder.row_queries", "decoder.row_self_value", "v51.self.v")
    kt = "v51.self.kt"
    nodes.append(helper.make_node("Transpose", [k], [kt], perm=[1, 0], name="V51.Self.KT"))
    scores_raw = "v51.self.scores_raw"
    nodes.append(helper.make_node("MatMul", [q, kt], [scores_raw], name="V51.Self.Scores"))
    scores = "v51.self.scores"
    nodes.append(helper.make_node("Div", [scores_raw, scale_name], [scores], name="V51.Self.Scale"))
    attention = "v51.self.attention"
    nodes.append(helper.make_node("Softmax", [scores], [attention], axis=1, name="V51.Self.Softmax"))
    context = "v51.self.context"
    nodes.append(helper.make_node("MatMul", [attention, v], [context], name="V51.Self.Context"))
    residual = "v51.self.residual"
    nodes.append(helper.make_node("Add", ["decoder.row_queries", context], [residual], name="V51.Self.Residual"))
    row_self = _layer_norm(nodes, residual, "decoder.row_self_norm", "v51.row_self")

    row_q = _linear(nodes, row_self, "decoder.row_query", "v51.cross.q")
    node_k = _linear(nodes, hidden, "decoder.node_key", "v51.cross.k")
    node_kt = "v51.cross.kt"
    nodes.append(helper.make_node("Transpose", [node_k], [node_kt], perm=[1, 0], name="V51.Cross.KT"))
    cross_raw = "v51.cross.scores_raw"
    nodes.append(helper.make_node("MatMul", [row_q, node_kt], [cross_raw], name="V51.Cross.Scores"))
    cross_scores = "v51.cross.scores"
    nodes.append(helper.make_node("Div", [cross_raw, scale_name], [cross_scores], name="V51.Cross.Scale"))
    cross_attention = "v51.cross.attention"
    nodes.append(helper.make_node("Softmax", [cross_scores], [cross_attention], axis=1, name="V51.Cross.Softmax"))
    cross_context = "v51.cross.context"
    nodes.append(helper.make_node("MatMul", [cross_attention, hidden], [cross_context], name="V51.Cross.Context"))
    cross_residual = "v51.cross.residual"
    nodes.append(helper.make_node("Add", [row_self, cross_context], [cross_residual], name="V51.Cross.Residual"))
    norm1 = _layer_norm(nodes, cross_residual, "decoder.row_norm1", "v51.row_norm1")
    ff1 = _linear(nodes, norm1, "decoder.row_feedforward.0", "v51.ff1")
    ff1 = _gelu(nodes, ff1, "V51.FFGELU")
    ff2 = _linear(nodes, ff1, "decoder.row_feedforward.2", "v51.ff2")
    ff_residual = "v51.ff.residual"
    nodes.append(helper.make_node("Add", [norm1, ff2], [ff_residual], name="V51.FF.Residual"))
    row_hidden = _layer_norm(nodes, ff_residual, "decoder.row_norm2", "v51.row_hidden")

    existence_column = _linear(nodes, row_hidden, "decoder.row_existence", "v51.row_existence_column")
    _squeeze(nodes, existence_column, "constant.axes1", "row_existence_logits")

    row_u = _unsqueeze(nodes, row_hidden, "constant.axes1", "v51.row_unsqueezed")
    field_u = _unsqueeze(nodes, "decoder.field_embedding.weight", "constant.axes0", "v51.field_unsqueezed")
    nodes.append(helper.make_node("Add", [row_u, field_u], ["field_query_states"], name="V51.FieldQueries"))

    node_keys = _linear(nodes, hidden, "decoder.node_pointer_key", "v51.node_keys")
    nodes.append(helper.make_node("Concat", [node_keys, "decoder.stop_node"], ["node_pointer_keys"], axis=0, name="V51.NodeKeysWithStop"))

    hidden_u = _unsqueeze(nodes, hidden, "constant.axes1", "v51.hidden_unsqueezed")
    token_shape = "v51.token_shape"
    nodes.append(helper.make_node("Shape", [token_states], [token_shape], name="V51.TokenShape"))
    hidden_expanded = "v51.hidden_expanded"
    nodes.append(helper.make_node("Expand", [hidden_u, token_shape], [hidden_expanded], name="V51.ExpandHidden"))
    contextual = "v51.contextual_tokens"
    nodes.append(helper.make_node("Concat", [token_states, hidden_expanded], [contextual], axis=2, name="V51.ContextualTokens"))
    _linear(nodes, contextual, "decoder.start_pointer_key", "start_pointer_keys")
    _linear(nodes, contextual, "decoder.end_pointer_key", "end_pointer_keys")
    _linear(nodes, contextual, "decoder.evidence_value", "evidence_values")
    nodes.append(helper.make_node("GreaterOrEqual", ["token_ids", byte_offset_name], ["token_valid_mask"], name="V51.TokenValid"))


def build_parser_v51_onnx_model(model, config) -> onnx.ModelProto:
    nodes, constants, hidden, encoded = _encoder_prefix(config)
    initializers = _state_initializers(model)
    existing = {value.name for value in initializers}
    initializers.extend(value for value in constants if value.name not in existing)
    _append_decoder(nodes=nodes, initializers=initializers, hidden=hidden, encoded=encoded, config=config)
    graph = helper.make_graph(
        nodes,
        "ParserV51NeuralMemory",
        [
            helper.make_tensor_value_info("token_ids", TensorProto.INT64, ["nodes", "text_bytes"]),
            helper.make_tensor_value_info("token_mask", TensorProto.BOOL, ["nodes", "text_bytes"]),
            helper.make_tensor_value_info("node_scalars", TensorProto.FLOAT, ["nodes", NODE_SCALAR_DIM]),
            helper.make_tensor_value_info("relation_features", TensorProto.FLOAT, ["nodes", "nodes", RELATION_FEATURE_DIM]),
        ],
        [
            helper.make_tensor_value_info("row_existence_logits", TensorProto.FLOAT, [config.max_rows]),
            helper.make_tensor_value_info("field_query_states", TensorProto.FLOAT, [config.max_rows, len(ROW_FIELD_ROLES), config.hidden_dim]),
            helper.make_tensor_value_info("node_pointer_keys", TensorProto.FLOAT, ["nodes_plus_stop", config.hidden_dim]),
            helper.make_tensor_value_info("start_pointer_keys", TensorProto.FLOAT, ["nodes", "text_bytes", config.hidden_dim]),
            helper.make_tensor_value_info("end_pointer_keys", TensorProto.FLOAT, ["nodes", "text_bytes", config.hidden_dim]),
            helper.make_tensor_value_info("evidence_values", TensorProto.FLOAT, ["nodes", "text_bytes", config.hidden_dim]),
            helper.make_tensor_value_info("token_valid_mask", TensorProto.BOOL, ["nodes", "text_bytes"]),
        ],
        initializer=initializers,
    )
    proto = helper.make_model(graph, producer_name="medicine-parser-v51", opset_imports=[helper.make_opsetid("", OPSET_VERSION)])
    proto.ir_version = ONNX_IR_VERSION
    onnx.checker.check_model(proto)
    return proto


def _runtime_nodes(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": str(node["node_id"]),
            "text": str(node["text"]),
            "detector_confidence": float(node["detector_confidence"]),
            "recognizer_confidence": float(node["recognizer_confidence"]),
            "polygon": node["polygon"],
        }
        for node in observation["nodes"]
        if str(node["text"]).strip()
    ]


def _memory_from_onnx(outputs: Sequence[np.ndarray]) -> ParserV51RuntimeMemory:
    return ParserV51RuntimeMemory(
        row_existence_logits=np.asarray(outputs[0]),
        field_query_states=np.asarray(outputs[1]),
        node_pointer_keys=np.asarray(outputs[2]),
        start_pointer_keys=np.asarray(outputs[3]),
        end_pointer_keys=np.asarray(outputs[4]),
        evidence_values=np.asarray(outputs[5]),
        token_valid_mask=np.asarray(outputs[6], dtype=bool),
    )


def _decoded_rows_match(paddle_rows: Sequence[Mapping[str, Any]], onnx_rows: Sequence[Mapping[str, Any]]) -> bool:
    if len(paddle_rows) != len(onnx_rows):
        return False
    for paddle_row, onnx_row in zip(paddle_rows, onnx_rows):
        paddle_body = {key: value for key, value in paddle_row.items() if key != "row_confidence"}
        onnx_body = {key: value for key, value in onnx_row.items() if key != "row_confidence"}
        if paddle_body != onnx_body:
            return False
        if abs(float(paddle_row["row_confidence"]) - float(onnx_row["row_confidence"])) > PARITY_TOLERANCE:
            return False
    return True


def run_parser_v51_export_parity_case(
    *,
    model,
    config,
    session: ort.InferenceSession,
    seed: int,
    require_decode_success: bool = True,
) -> dict[str, Any]:
    truth = generate_parser_world(
        seed=seed,
        document_index=0,
        profile=ParserWorldProfile(medication_count=(2, 4), distractor_section_count=(2, 5), product_vocabulary="unseen", wording_vocabulary="unseen"),
    )
    observation = simulate_observations(
        truth,
        seed=seed + 1,
        profile=ObservationProfile(
            text_corruption_rate=0.12,
            drop_rate=0.04,
            duplicate_rate=0.03,
            split_rate=0.10,
            merge_rate=0.12,
            geometry_jitter=0.005,
            false_positive_count=(1, 4),
            reading_order_shuffle_rate=0.08,
        ),
    )
    nodes = _runtime_nodes(observation)
    document = build_parser_v5_runtime_document_input(
        document_id=str(truth["document_id"]), width=truth["width"], height=truth["height"], nodes=nodes, max_text_bytes=config.max_text_bytes
    )
    by_id = {str(node["node_id"]): node for node in nodes}
    canonical_nodes = tuple(by_id[node_id] for node_id in document.node_ids)
    tensors = parser_v5_document_tensors(document)
    with paddle.no_grad():
        paddle_memory = runtime_memory_from_paddle(model(tensors))
    feeds = {
        "token_ids": tensors.token_ids.numpy(),
        "token_mask": tensors.token_mask.numpy(),
        "node_scalars": tensors.node_scalars.numpy(),
        "relation_features": tensors.relation_features.numpy(),
    }
    outputs = session.run(list(_OUTPUT_NAMES), feeds)
    onnx_memory = _memory_from_onnx(outputs)
    paddle_arrays = [getattr(paddle_memory, name) for name in _OUTPUT_NAMES[:-1]]
    onnx_arrays = [getattr(onnx_memory, name) for name in _OUTPUT_NAMES[:-1]]
    deltas = [float(np.max(np.abs(a - b))) if a.size else 0.0 for a, b in zip(paddle_arrays, onnx_arrays)]
    mask_equal = bool(np.array_equal(paddle_memory.token_valid_mask, onnx_memory.token_valid_mask))
    decoded_rows_equal = False
    decode_status = "ok"
    try:
        paddle_rows = decode_parser_v51_memory(nodes=canonical_nodes, memory=paddle_memory)
        onnx_rows = decode_parser_v51_memory(nodes=canonical_nodes, memory=onnx_memory)
        decoded_rows_equal = _decoded_rows_match(paddle_rows, onnx_rows)
    except ValueError as exc:
        if require_decode_success:
            raise
        decode_status = f"rejected:{exc}"
        decoded_rows_equal = True
    return {
        "seed": seed,
        "nodes": len(canonical_nodes),
        "max_abs_delta": max(deltas, default=0.0),
        "token_valid_mask_equal": mask_equal,
        "decoded_rows_equal": decoded_rows_equal,
        "decode_status": decode_status,
    }


def export_parser_v51_candidate(*, candidate_freeze: str | Path, training_result: str | Path, output_dir: str | Path) -> dict[str, Any]:
    freeze = load_parser_v51_candidate_freeze(candidate_freeze)
    validate_parser_v51_frozen_implementation(freeze)
    result_path = Path(training_result).resolve()
    if _sha256_file(result_path) != freeze["training_result_sha256"]:
        raise ValueError("Parser v5.1 export training result disagrees with candidate freeze")
    model, config = load_trained_parser_v51_model(result_path, device="cpu")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_output_lock(root):
        model_path = root / MODEL_FILE
        manifest_path = root / MANIFEST_FILE
        if model_path.exists() or manifest_path.exists():
            if not model_path.is_file() or not manifest_path.is_file():
                raise ValueError("Parser v5.1 export output is incomplete")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_candidate_freeze_fingerprint") != freeze["freeze_fingerprint"]:
                raise ValueError("Parser v5.1 existing export freeze mismatch")
            if manifest.get("model_sha256") != _sha256_file(model_path):
                raise ValueError("Parser v5.1 existing export model SHA mismatch")
            if manifest.get("export_implementation_sha256") != _export_identity():
                raise ValueError("Parser v5.1 existing export implementation mismatch")
            return manifest
        onnx.save(build_parser_v51_onnx_model(model, config), model_path)
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        parity = [run_parser_v51_export_parity_case(model=model, config=config, session=session, seed=seed) for seed in (9173, 18341, 29663)]
        max_delta = max(item["max_abs_delta"] for item in parity)
        if max_delta > PARITY_TOLERANCE or not all(item["token_valid_mask_equal"] and item["decoded_rows_equal"] for item in parity):
            raise ValueError("Parser v5.1 ONNX parity validation failed")
        manifest = {
            "schema_version": 1,
            "status": "ok",
            "model_id": "parser_v51_direct_rows_v1",
            "source_candidate_freeze_fingerprint": freeze["freeze_fingerprint"],
            "training_result_sha256": freeze["training_result_sha256"],
            "checkpoint_sha256": freeze["checkpoint_sha256"],
            "model_file": MODEL_FILE,
            "model_sha256": _sha256_file(model_path),
            "opset_version": OPSET_VERSION,
            "parity_tolerance": PARITY_TOLERANCE,
            "parity": parity,
            "outputs": list(_OUTPUT_NAMES),
            "export_implementation_sha256": _export_identity(),
        }
        atomic_write(manifest_path, _canonical_json(manifest) + b"\n")
        return manifest


__all__ = ["build_parser_v51_onnx_model", "export_parser_v51_candidate", "run_parser_v51_export_parity_case"]
