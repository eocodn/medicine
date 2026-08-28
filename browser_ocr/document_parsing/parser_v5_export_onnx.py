from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import onnx
import onnxruntime as ort
import paddle
from onnx import TensorProto, helper, numpy_helper

from .artifact_storage import exclusive_output_lock
from .parser_v5_export_parity import (
    load_frozen_parser_v5_export_model,
    run_parser_v5_export_parity_case,
)
from .parser_v5_heads_paddle import FIELD_ROLE_LABELS
from .parser_v5_model_input import (
    BYTE_PAD,
    NODE_SCALAR_DIM,
    PARSER_V5_ROLE_LABELS,
    RELATION_FEATURE_DIM,
)
from .parser_v5_training_paddle import ParserV5Model, ParserV5TrainingConfig
from .parser_v5_validation_protocol import (
    load_parser_v5_candidate_freeze,
    validate_parser_v5_frozen_implementation,
)


MODEL_FILE = "parser.onnx"
MANIFEST_FILE = "manifest.json"
ONNX_IR_VERSION = 8
OPSET_VERSION = 17
PARITY_TOLERANCE = 2e-5
_EXPORT_IMPLEMENTATION_FILES = (
    "parser_v5_export_onnx.py",
    "parser_v5_export_parity.py",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_implementation_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    return {name: _sha256_file(root / name) for name in _EXPORT_IMPLEMENTATION_FILES}


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(_canonical_json(value) + b"\n")
    os.replace(temporary, path)


def _tensor(name: str, value: np.ndarray) -> onnx.TensorProto:
    return numpy_helper.from_array(np.asarray(value), name=name)


def _state_initializers(model: paddle.nn.Layer) -> list[onnx.TensorProto]:
    values: list[onnx.TensorProto] = []
    for name, parameter in model.state_dict().items():
        array = parameter.numpy()
        if array.dtype.kind == "f":
            array = np.asarray(array, dtype=np.float32)
        if name == "encoder.text_encoder.embedding.weight":
            # Paddle's Embedding padding_idx semantics are applied at lookup
            # time, not guaranteed by the persisted parameter value. AdamW
            # weight decay can therefore move the stored PAD row away from
            # zero while Paddle inference still returns an all-zero embedding
            # for BYTE_PAD. A raw ONNX Gather would otherwise leak that stored
            # row and diverge from the trained model's actual forward pass.
            array = array.copy()
            array[BYTE_PAD] = 0.0
        values.append(_tensor(name, array))
    return values


def _linear(nodes: list[onnx.NodeProto], source: str, prefix: str, output: str) -> str:
    product = f"{output}.matmul"
    nodes.append(helper.make_node("MatMul", [source, f"{prefix}.weight"], [product], name=f"{prefix}.MatMul"))
    nodes.append(helper.make_node("Add", [product, f"{prefix}.bias"], [output], name=f"{prefix}.Bias"))
    return output


def _gelu(nodes: list[onnx.NodeProto], source: str, prefix: str) -> str:
    normalized = f"{prefix}.normalized"
    erf = f"{prefix}.erf"
    shifted = f"{prefix}.shifted"
    product = f"{prefix}.product"
    output = f"{prefix}.output"
    nodes.append(helper.make_node("Div", [source, "constant.sqrt2"], [normalized], name=f"{prefix}.Div"))
    nodes.append(helper.make_node("Erf", [normalized], [erf], name=f"{prefix}.Erf"))
    nodes.append(helper.make_node("Add", [erf, "constant.one"], [shifted], name=f"{prefix}.AddOne"))
    nodes.append(helper.make_node("Mul", [source, shifted], [product], name=f"{prefix}.Mul"))
    nodes.append(helper.make_node("Mul", [product, "constant.half"], [output], name=f"{prefix}.Half"))
    return output


def _unsqueeze(nodes: list[onnx.NodeProto], source: str, axes: str, output: str) -> str:
    nodes.append(helper.make_node("Unsqueeze", [source, axes], [output], name=f"{output}.Unsqueeze"))
    return output


def _squeeze(nodes: list[onnx.NodeProto], source: str, axes: str, output: str) -> str:
    nodes.append(helper.make_node("Squeeze", [source, axes], [output], name=f"{output}.Squeeze"))
    return output


def _layer_norm(nodes: list[onnx.NodeProto], source: str, prefix: str, output: str) -> str:
    nodes.append(
        helper.make_node(
            "LayerNormalization",
            [source, f"{prefix}.weight", f"{prefix}.bias"],
            [output],
            axis=-1,
            epsilon=1e-5,
            name=f"{prefix}.LayerNormalization",
        )
    )
    return output


def _shape_axis(nodes: list[onnx.NodeProto], source: str, axis: int, prefix: str) -> str:
    shape = f"{prefix}.shape"
    output = f"{prefix}.dim"
    nodes.append(helper.make_node("Shape", [source], [shape], name=f"{prefix}.Shape"))
    nodes.append(
        helper.make_node(
            "Gather",
            [shape, f"constant.index{axis}"],
            [output],
            axis=0,
            name=f"{prefix}.GatherDim",
        )
    )
    return output


def _build_onnx_model(model: ParserV5Model, config: ParserV5TrainingConfig) -> onnx.ModelProto:
    spec = config.encoder_spec
    nodes: list[onnx.NodeProto] = []
    initializers = _state_initializers(model)
    constants = {
        "constant.index0": np.asarray(0, dtype=np.int64),
        "constant.index1": np.asarray(1, dtype=np.int64),
        "constant.index2": np.asarray(2, dtype=np.int64),
        "constant.axes0": np.asarray([0], dtype=np.int64),
        "constant.axes1": np.asarray([1], dtype=np.int64),
        "constant.axes2": np.asarray([2], dtype=np.int64),
        "constant.axes_last": np.asarray([-1], dtype=np.int64),
        "constant.one": np.asarray(1.0, dtype=np.float32),
        "constant.half": np.asarray(0.5, dtype=np.float32),
        "constant.sqrt2": np.asarray(math.sqrt(2.0), dtype=np.float32),
        "constant.neg_large": np.asarray(-1e4, dtype=np.float32),
        "constant.pos_large": np.asarray(1e4, dtype=np.float32),
        "constant.hidden_dim": np.asarray([spec.hidden_dim], dtype=np.int64),
        "constant.attention_reshape": np.asarray([0, 3, spec.heads, spec.hidden_dim // spec.heads], dtype=np.int64),
        "constant.relation_value_reshape": np.asarray([0, 0, spec.heads, spec.hidden_dim // spec.heads], dtype=np.int64),
        "constant.context_reshape": np.asarray([0, spec.hidden_dim], dtype=np.int64),
    }
    initializers.extend(_tensor(name, value) for name, value in constants.items())

    embedded = "text.embedding"
    nodes.append(
        helper.make_node(
            "Gather",
            ["encoder.text_encoder.embedding.weight", "token_ids"],
            [embedded],
            axis=0,
            name="TextEmbedding.Gather",
        )
    )
    embedded_t = "text.embedding_transposed"
    nodes.append(helper.make_node("Transpose", [embedded], [embedded_t], perm=[0, 2, 1], name="TextEmbedding.Transpose"))
    conv3 = "text.conv3"
    conv5 = "text.conv5"
    nodes.append(
        helper.make_node(
            "Conv",
            [embedded_t, "encoder.text_encoder.conv3.weight", "encoder.text_encoder.conv3.bias"],
            [conv3],
            pads=[1, 1],
            strides=[1],
            name="TextConv3.Conv",
        )
    )
    nodes.append(
        helper.make_node(
            "Conv",
            [embedded_t, "encoder.text_encoder.conv5.weight", "encoder.text_encoder.conv5.bias"],
            [conv5],
            pads=[2, 2],
            strides=[1],
            name="TextConv5.Conv",
        )
    )
    conv3_gelu = _gelu(nodes, conv3, "TextConv3.GELU")
    conv5_gelu = _gelu(nodes, conv5, "TextConv5.GELU")
    encoded = "text.encoded"
    nodes.append(helper.make_node("Concat", [conv3_gelu, conv5_gelu], [encoded], axis=1, name="Text.ConcatConv"))

    mask_float = "text.mask_float"
    nodes.append(helper.make_node("Cast", ["token_mask"], [mask_float], to=TensorProto.FLOAT, name="Text.CastMask"))
    mask_3d = _unsqueeze(nodes, mask_float, "constant.axes1", "text.mask_3d")
    denominator = "text.denominator"
    nodes.append(helper.make_node("ReduceSum", [mask_3d, "constant.axes2"], [denominator], keepdims=0, name="Text.MaskSum"))
    denominator_bounded = "text.denominator_bounded"
    nodes.append(helper.make_node("Max", [denominator, "constant.one"], [denominator_bounded], name="Text.BoundMaskSum"))
    masked_sum_input = "text.masked_sum_input"
    nodes.append(helper.make_node("Mul", [encoded, mask_3d], [masked_sum_input], name="Text.ApplyMask"))
    summed = "text.summed"
    nodes.append(helper.make_node("ReduceSum", [masked_sum_input, "constant.axes2"], [summed], keepdims=0, name="Text.ReduceMeanSum"))
    mean_pool = "text.mean_pool"
    nodes.append(helper.make_node("Div", [summed, denominator_bounded], [mean_pool], name="Text.MeanPool"))
    token_mask_3d = _unsqueeze(nodes, "token_mask", "constant.axes1", "text.bool_mask_3d")
    max_masked = "text.max_masked"
    nodes.append(helper.make_node("Where", [token_mask_3d, encoded, "constant.neg_large"], [max_masked], name="Text.MaxMask"))
    max_pool = "text.max_pool"
    nodes.append(helper.make_node("ReduceMax", [max_masked], [max_pool], axes=[2], keepdims=0, name="Text.MaxPool"))
    text_features = "text.features"
    nodes.append(helper.make_node("Concat", [mean_pool, max_pool], [text_features], axis=1, name="Text.ConcatPools"))

    combined_input = "encoder.input"
    nodes.append(helper.make_node("Concat", [text_features, "node_scalars"], [combined_input], axis=1, name="Encoder.ConcatInput"))
    projected = _linear(nodes, combined_input, "encoder.input_projection", "encoder.input_projection.output")
    hidden = _gelu(nodes, projected, "Encoder.InputGELU")

    for layer_index in range(spec.layers):
        prefix = f"encoder.layers.{layer_index}"
        qkv = _linear(nodes, hidden, f"{prefix}.qkv", f"layer{layer_index}.qkv")
        qkv_reshaped = f"layer{layer_index}.qkv_reshaped"
        nodes.append(helper.make_node("Reshape", [qkv, "constant.attention_reshape"], [qkv_reshaped], name=f"Layer{layer_index}.QKVReshape"))
        qkv_t = f"layer{layer_index}.qkv_transposed"
        nodes.append(helper.make_node("Transpose", [qkv_reshaped], [qkv_t], perm=[1, 2, 0, 3], name=f"Layer{layer_index}.QKVTranspose"))
        query = f"layer{layer_index}.query"
        key = f"layer{layer_index}.key"
        value = f"layer{layer_index}.value"
        nodes.append(helper.make_node("Gather", [qkv_t, "constant.index0"], [query], axis=0, name=f"Layer{layer_index}.Query"))
        nodes.append(helper.make_node("Gather", [qkv_t, "constant.index1"], [key], axis=0, name=f"Layer{layer_index}.Key"))
        nodes.append(helper.make_node("Gather", [qkv_t, "constant.index2"], [value], axis=0, name=f"Layer{layer_index}.Value"))
        key_t = f"layer{layer_index}.key_transposed"
        nodes.append(helper.make_node("Transpose", [key], [key_t], perm=[0, 2, 1], name=f"Layer{layer_index}.KeyTranspose"))
        scores_raw = f"layer{layer_index}.scores_raw"
        nodes.append(helper.make_node("MatMul", [query, key_t], [scores_raw], name=f"Layer{layer_index}.Scores"))
        scale_name = f"constant.attention_scale_{layer_index}"
        initializers.append(_tensor(scale_name, np.asarray(math.sqrt(spec.hidden_dim // spec.heads), dtype=np.float32)))
        scores = f"layer{layer_index}.scores"
        nodes.append(helper.make_node("Div", [scores_raw, scale_name], [scores], name=f"Layer{layer_index}.Scale"))

        relation_bias = _linear(nodes, "relation_features", f"{prefix}.relation_bias", f"layer{layer_index}.relation_bias")
        relation_bias_t = f"layer{layer_index}.relation_bias_t"
        nodes.append(helper.make_node("Transpose", [relation_bias], [relation_bias_t], perm=[2, 1, 0], name=f"Layer{layer_index}.RelationBiasTranspose"))
        biased = f"layer{layer_index}.biased_scores"
        nodes.append(helper.make_node("Add", [scores, relation_bias_t], [biased], name=f"Layer{layer_index}.AddRelationBias"))
        attention = f"layer{layer_index}.attention"
        nodes.append(helper.make_node("Softmax", [biased], [attention], axis=-1, name=f"Layer{layer_index}.Softmax"))
        node_context = f"layer{layer_index}.node_context"
        nodes.append(helper.make_node("MatMul", [attention, value], [node_context], name=f"Layer{layer_index}.NodeContext"))

        relation_value = _linear(nodes, "relation_features", f"{prefix}.relation_value", f"layer{layer_index}.relation_value")
        relation_value_reshaped = f"layer{layer_index}.relation_value_reshaped"
        nodes.append(helper.make_node("Reshape", [relation_value, "constant.relation_value_reshape"], [relation_value_reshaped], name=f"Layer{layer_index}.RelationValueReshape"))
        relation_value_t = f"layer{layer_index}.relation_value_t"
        nodes.append(helper.make_node("Transpose", [relation_value_reshaped], [relation_value_t], perm=[2, 1, 0, 3], name=f"Layer{layer_index}.RelationValueTranspose"))
        attention_4d = _unsqueeze(nodes, attention, "constant.axes_last", f"layer{layer_index}.attention_4d")
        weighted_relation = f"layer{layer_index}.weighted_relation"
        nodes.append(helper.make_node("Mul", [attention_4d, relation_value_t], [weighted_relation], name=f"Layer{layer_index}.WeightRelation"))
        relation_context = f"layer{layer_index}.relation_context"
        nodes.append(helper.make_node("ReduceSum", [weighted_relation, "constant.axes2"], [relation_context], keepdims=0, name=f"Layer{layer_index}.RelationContext"))
        context_sum = f"layer{layer_index}.context_sum"
        nodes.append(helper.make_node("Add", [node_context, relation_context], [context_sum], name=f"Layer{layer_index}.AddContexts"))
        context_t = f"layer{layer_index}.context_t"
        nodes.append(helper.make_node("Transpose", [context_sum], [context_t], perm=[1, 0, 2], name=f"Layer{layer_index}.ContextTranspose"))
        context = f"layer{layer_index}.context"
        nodes.append(helper.make_node("Reshape", [context_t, "constant.context_reshape"], [context], name=f"Layer{layer_index}.ContextReshape"))
        output = _linear(nodes, context, f"{prefix}.output", f"layer{layer_index}.output")
        residual1 = f"layer{layer_index}.residual1"
        nodes.append(helper.make_node("Add", [hidden, output], [residual1], name=f"Layer{layer_index}.Residual1"))
        norm1 = _layer_norm(nodes, residual1, f"{prefix}.norm1", f"layer{layer_index}.norm1")
        ff1 = _linear(nodes, norm1, f"{prefix}.feedforward.0", f"layer{layer_index}.ff1")
        ff1_gelu = _gelu(nodes, ff1, f"Layer{layer_index}.FFGELU")
        ff2 = _linear(nodes, ff1_gelu, f"{prefix}.feedforward.2", f"layer{layer_index}.ff2")
        residual2 = f"layer{layer_index}.residual2"
        nodes.append(helper.make_node("Add", [norm1, ff2], [residual2], name=f"Layer{layer_index}.Residual2"))
        hidden = _layer_norm(nodes, residual2, f"{prefix}.norm2", f"layer{layer_index}.norm2")

    _linear(nodes, hidden, "encoder.role_head", "role_logits")
    candidate_column = _linear(nodes, hidden, "heads.candidate_head", "candidate_column")
    _squeeze(nodes, candidate_column, "constant.axes1", "candidate_logits")

    field_hidden = "assignment.field_hidden"
    nodes.append(helper.make_node("Gather", [hidden, "field_node_index"], [field_hidden], axis=0, name="Assignment.GatherFields"))
    role_hidden = "assignment.role_hidden"
    nodes.append(helper.make_node("Gather", ["heads.role_embedding.weight", "field_role_index"], [role_hidden], axis=0, name="Assignment.GatherRoles"))
    none_input = "assignment.none_input"
    nodes.append(helper.make_node("Concat", [field_hidden, role_hidden], [none_input], axis=1, name="Assignment.ConcatNone"))
    none_hidden = _linear(nodes, none_input, "heads.none_hidden", "assignment.none_hidden")
    none_gelu = _gelu(nodes, none_hidden, "Assignment.NoneGELU")
    none_logits = _linear(nodes, none_gelu, "heads.none_output", "assignment.none_logits")

    slot_hidden = "assignment.slot_hidden"
    nodes.append(helper.make_node("MatMul", ["product_membership", hidden], [slot_hidden], name="Assignment.PoolProducts"))
    selected_relations = "assignment.selected_relations"
    nodes.append(helper.make_node("Gather", ["relation_features", "field_node_index"], [selected_relations], axis=0, name="Assignment.GatherRelations"))
    pooled_relations = "assignment.pooled_relations"
    nodes.append(
        helper.make_node(
            "Einsum",
            [selected_relations, "product_membership"],
            [pooled_relations],
            equation="fnd,pn->fpd",
            name="Assignment.PoolRelations",
        )
    )

    field_count = _shape_axis(nodes, "field_node_index", 0, "assignment.field_count")
    product_count = _shape_axis(nodes, "product_membership", 0, "assignment.product_count")
    field_count_1d = _unsqueeze(nodes, field_count, "constant.axes0", "assignment.field_count_1d")
    product_count_1d = _unsqueeze(nodes, product_count, "constant.axes0", "assignment.product_count_1d")
    expand_shape = "assignment.expand_shape"
    nodes.append(helper.make_node("Concat", [field_count_1d, product_count_1d, "constant.hidden_dim"], [expand_shape], axis=0, name="Assignment.HiddenExpandShape"))
    field_unsqueezed = _unsqueeze(nodes, field_hidden, "constant.axes1", "assignment.field_unsqueezed")
    field_expanded = "assignment.field_expanded"
    nodes.append(helper.make_node("Expand", [field_unsqueezed, expand_shape], [field_expanded], name="Assignment.ExpandFields"))
    slot_unsqueezed = _unsqueeze(nodes, slot_hidden, "constant.axes0", "assignment.slot_unsqueezed")
    slot_expanded = "assignment.slot_expanded"
    nodes.append(helper.make_node("Expand", [slot_unsqueezed, expand_shape], [slot_expanded], name="Assignment.ExpandProducts"))

    role_dim = config.role_embedding_dim
    role_dim_name = "constant.role_embedding_dim"
    initializers.append(_tensor(role_dim_name, np.asarray([role_dim], dtype=np.int64)))
    role_expand_shape = "assignment.role_expand_shape"
    nodes.append(helper.make_node("Concat", [field_count_1d, product_count_1d, role_dim_name], [role_expand_shape], axis=0, name="Assignment.RoleExpandShape"))
    role_unsqueezed = _unsqueeze(nodes, role_hidden, "constant.axes1", "assignment.role_unsqueezed")
    role_expanded = "assignment.role_expanded"
    nodes.append(helper.make_node("Expand", [role_unsqueezed, role_expand_shape], [role_expanded], name="Assignment.ExpandRoles"))
    pair_input = "assignment.pair_input"
    nodes.append(helper.make_node("Concat", [field_expanded, slot_expanded, role_expanded, pooled_relations], [pair_input], axis=2, name="Assignment.ConcatPairs"))
    assignment_hidden = _linear(nodes, pair_input, "heads.assignment_hidden", "assignment.hidden")
    assignment_gelu = _gelu(nodes, assignment_hidden, "Assignment.PairGELU")
    product_logits_column = _linear(nodes, assignment_gelu, "heads.assignment_output", "assignment.product_logits_column")
    product_logits = _squeeze(nodes, product_logits_column, "constant.axes2", "assignment.product_logits")
    unavailable_bool = "assignment.unavailable_bool"
    nodes.append(helper.make_node("Not", ["product_available"], [unavailable_bool], name="Assignment.NotAvailable"))
    unavailable = "assignment.unavailable"
    nodes.append(helper.make_node("Cast", [unavailable_bool], [unavailable], to=TensorProto.FLOAT, name="Assignment.CastUnavailable"))
    unavailable_row = _unsqueeze(nodes, unavailable, "constant.axes0", "assignment.unavailable_row")
    unavailable_penalty = "assignment.unavailable_penalty"
    nodes.append(helper.make_node("Mul", [unavailable_row, "constant.pos_large"], [unavailable_penalty], name="Assignment.UnavailablePenalty"))
    product_logits_masked = "assignment.product_logits_masked"
    nodes.append(helper.make_node("Sub", [product_logits, unavailable_penalty], [product_logits_masked], name="Assignment.MaskUnavailable"))
    nodes.append(helper.make_node("Concat", [product_logits_masked, none_logits], ["assignment_logits"], axis=1, name="Assignment.ConcatNoneLogit"))

    graph = helper.make_graph(
        nodes,
        "ParserV5GlobalStructured",
        [
            helper.make_tensor_value_info("token_ids", TensorProto.INT64, ["nodes", "text_bytes"]),
            helper.make_tensor_value_info("token_mask", TensorProto.BOOL, ["nodes", "text_bytes"]),
            helper.make_tensor_value_info("node_scalars", TensorProto.FLOAT, ["nodes", NODE_SCALAR_DIM]),
            helper.make_tensor_value_info("relation_features", TensorProto.FLOAT, ["nodes", "nodes", RELATION_FEATURE_DIM]),
            helper.make_tensor_value_info("product_membership", TensorProto.FLOAT, ["products", "nodes"]),
            helper.make_tensor_value_info("product_available", TensorProto.BOOL, ["products"]),
            helper.make_tensor_value_info("field_node_index", TensorProto.INT64, ["fields"]),
            helper.make_tensor_value_info("field_role_index", TensorProto.INT64, ["fields"]),
        ],
        [
            helper.make_tensor_value_info("role_logits", TensorProto.FLOAT, ["nodes", len(PARSER_V5_ROLE_LABELS)]),
            helper.make_tensor_value_info("candidate_logits", TensorProto.FLOAT, ["nodes"]),
            helper.make_tensor_value_info("assignment_logits", TensorProto.FLOAT, ["fields", "assignment_classes"]),
        ],
        initializer=initializers,
    )
    model_proto = helper.make_model(
        graph,
        producer_name="medicine-parser-v5",
        opset_imports=[helper.make_opsetid("", OPSET_VERSION)],
    )
    model_proto.ir_version = ONNX_IR_VERSION
    onnx.checker.check_model(model_proto)
    return model_proto


def _validate_existing(root: Path, freeze: Mapping[str, Any]) -> dict[str, Any]:
    manifest_path = root / MANIFEST_FILE
    model_path = root / MODEL_FILE
    manifest = _json_object(manifest_path, label="Parser v5 export manifest")
    if manifest.get("status") != "ok" or manifest.get("source_candidate_freeze_fingerprint") != freeze["freeze_fingerprint"]:
        raise ValueError("Parser v5 export manifest candidate freeze mismatch")
    if not model_path.is_file() or manifest.get("model_sha256") != _sha256_file(model_path):
        raise ValueError("Parser v5 export model SHA-256 mismatch")
    if manifest.get("export_implementation_sha256") != _export_implementation_hashes():
        raise ValueError("Parser v5 export implementation identity mismatch")
    return manifest


def export_parser_v5_candidate(
    *,
    candidate_freeze: str | Path,
    training_result: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    freeze = load_parser_v5_candidate_freeze(candidate_freeze)
    validate_parser_v5_frozen_implementation(freeze)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_output_lock(root):
        if (root / MANIFEST_FILE).exists():
            return _validate_existing(root, freeze)
        model, config = load_frozen_parser_v5_export_model(Path(training_result).resolve(), freeze)
        model_proto = _build_onnx_model(model, config)
        model_bytes = model_proto.SerializeToString(deterministic=True)
        model_path = root / MODEL_FILE
        model_path.write_bytes(model_bytes)
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        parity_cases = [
            run_parser_v5_export_parity_case(model, config, seed=7301, session=session),
            run_parser_v5_export_parity_case(
                model,
                config,
                seed=7302,
                session=session,
                empty_assignments=True,
            ),
        ]
        parity_max = max(float(item["max_abs_delta"]) for item in parity_cases)
        if parity_max > PARITY_TOLERANCE:
            model_path.unlink(missing_ok=True)
            raise ValueError(f"Parser v5 ONNX parity delta {parity_max} exceeds tolerance {PARITY_TOLERANCE}")
        manifest = {
            "schema_version": 1,
            "status": "ok",
            "model_id": "parser_v5_global_structured_v1",
            "model_format": "onnx",
            "model_file": MODEL_FILE,
            "model_sha256": _sha256_file(model_path),
            "source_candidate_freeze_fingerprint": str(freeze["freeze_fingerprint"]),
            "source_checkpoint_sha256": str(freeze["checkpoint_sha256"]),
            "export_implementation_sha256": _export_implementation_hashes(),
            "decode_policy": dict(freeze["decode_policy"]),
            "role_labels": list(PARSER_V5_ROLE_LABELS),
            "field_role_labels": list(FIELD_ROLE_LABELS),
            "inputs": [
                {"name": "token_ids", "dtype": "int64", "shape": ["nodes", "text_bytes"]},
                {"name": "token_mask", "dtype": "bool", "shape": ["nodes", "text_bytes"]},
                {"name": "node_scalars", "dtype": "float32", "shape": ["nodes", NODE_SCALAR_DIM]},
                {"name": "relation_features", "dtype": "float32", "shape": ["nodes", "nodes", RELATION_FEATURE_DIM]},
                {"name": "product_membership", "dtype": "float32", "shape": ["products", "nodes"]},
                {"name": "product_available", "dtype": "bool", "shape": ["products"]},
                {"name": "field_node_index", "dtype": "int64", "shape": ["fields"]},
                {"name": "field_role_index", "dtype": "int64", "shape": ["fields"]},
            ],
            "outputs": [
                {"name": "role_logits", "dtype": "float32", "shape": ["nodes", len(PARSER_V5_ROLE_LABELS)]},
                {"name": "candidate_logits", "dtype": "float32", "shape": ["nodes"]},
                {"name": "assignment_logits", "dtype": "float32", "shape": ["fields", "products_plus_none"]},
            ],
            "toolchain": {
                "onnx": onnx.__version__,
                "onnxruntime": ort.__version__,
                "paddle": paddle.__version__,
                "ir_version": ONNX_IR_VERSION,
                "opset_version": OPSET_VERSION,
                "converter": "direct_onnx_builder_v1",
            },
            "parity_tolerance": PARITY_TOLERANCE,
            "parity_reference": {"framework": "paddle", "device": "cpu"},
            "parity_max_abs_delta": parity_max,
            "parity_cases": parity_cases,
        }
        _atomic_json(root / MANIFEST_FILE, manifest)
        return manifest


__all__ = ["export_parser_v5_candidate"]