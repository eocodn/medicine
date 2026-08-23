from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import onnx
import onnxruntime as ort
import paddle
from onnx import TensorProto, helper, numpy_helper

from .document_graph import EDGE_FEATURE_DIM, NODE_FEATURE_DIM, ROLE_LABELS
from .graph_inference_paddle import load_graph_model


MANIFEST_FILE = "manifest.json"
MODEL_FILE = "parser.onnx"
ONNX_IR_VERSION = 8
OPSET_VERSION = 17
PARITY_TOLERANCE = 1e-5


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(_canonical_json(value) + b"\n")
    os.replace(temporary, path)


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read graph export JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"graph export JSON must contain an object: {path}")
    return value


def _implementation_sha256() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for source in (
        root / "document_graph.py",
        root / "graph_encoder_paddle.py",
        root / "graph_inference_paddle.py",
        Path(__file__).resolve(),
    ):
        name = source.name.encode("utf-8")
        content = source.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _toolchain() -> dict[str, str | int]:
    return {
        "onnx": onnx.__version__,
        "onnxruntime": ort.__version__,
        "paddle": paddle.__version__,
        "ir_version": ONNX_IR_VERSION,
        "opset_version": OPSET_VERSION,
    }


def _tensor(name: str, value: np.ndarray) -> onnx.TensorProto:
    return numpy_helper.from_array(np.asarray(value), name=name)


def _state_initializers(model: paddle.nn.Layer) -> list[onnx.TensorProto]:
    initializers: list[onnx.TensorProto] = []
    for name, parameter in model.state_dict().items():
        value = np.asarray(parameter.numpy(), dtype=np.float32)
        initializers.append(_tensor(name, value))
    return initializers


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


def _linear(nodes: list[onnx.NodeProto], source: str, parameter_prefix: str, output: str) -> str:
    product = f"{output}.matmul"
    nodes.append(
        helper.make_node(
            "MatMul",
            [source, f"{parameter_prefix}.weight"],
            [product],
            name=f"{parameter_prefix}.MatMul",
        )
    )
    nodes.append(
        helper.make_node(
            "Add",
            [product, f"{parameter_prefix}.bias"],
            [output],
            name=f"{parameter_prefix}.Bias",
        )
    )
    return output


def _column(nodes: list[onnx.NodeProto], source: str, column: int, output: str) -> str:
    nodes.append(
        helper.make_node(
            "Gather",
            [source, f"constant.index{column}"],
            [output],
            axis=1,
            name=f"{output}.Gather",
        )
    )
    return output


def _build_onnx_model(model: paddle.nn.Layer) -> onnx.ModelProto:
    spec = model.spec
    nodes: list[onnx.NodeProto] = []
    initializers = _state_initializers(model)
    initializers.extend(
        [
            _tensor("constant.index0", np.asarray(0, dtype=np.int64)),
            _tensor("constant.index1", np.asarray(1, dtype=np.int64)),
            _tensor("constant.axis1", np.asarray([1], dtype=np.int64)),
            _tensor("constant.onehot_values", np.asarray([0.0, 1.0], dtype=np.float32)),
            _tensor("constant.sqrt2", np.asarray(np.sqrt(2.0), dtype=np.float32)),
            _tensor("constant.one", np.asarray(1.0, dtype=np.float32)),
            _tensor("constant.half", np.asarray(0.5, dtype=np.float32)),
            _tensor("constant.minimum_degree", np.asarray(1.0, dtype=np.float32)),
        ]
    )

    projected = _linear(nodes, "node_features", "input_projection", "input_projection.output")
    hidden = _gelu(nodes, projected, "input_projection.gelu")

    for layer_index in range(spec.layers):
        prefix = f"layers.{layer_index}"
        source_index = _column(nodes, "edge_index", 0, f"{prefix}.source_index")
        target_index = _column(nodes, "edge_index", 1, f"{prefix}.target_index")
        source_hidden = f"{prefix}.source_hidden"
        nodes.append(
            helper.make_node(
                "Gather",
                [hidden, source_index],
                [source_hidden],
                axis=0,
                name=f"{prefix}.GatherSource",
            )
        )
        neighbor = _linear(nodes, source_hidden, f"{prefix}.neighbor_projection", f"{prefix}.neighbor")
        edge = _linear(nodes, "edge_features", f"{prefix}.edge_projection", f"{prefix}.edge")
        messages = f"{prefix}.messages"
        nodes.append(helper.make_node("Add", [neighbor, edge], [messages], name=f"{prefix}.Messages"))

        hidden_shape = f"{prefix}.hidden_shape"
        node_count = f"{prefix}.node_count"
        one_hot = f"{prefix}.target_onehot"
        target_matrix = f"{prefix}.target_matrix"
        aggregate = f"{prefix}.aggregate"
        degree = f"{prefix}.degree"
        bounded_degree = f"{prefix}.bounded_degree"
        mean_aggregate = f"{prefix}.mean_aggregate"
        nodes.append(helper.make_node("Shape", [hidden], [hidden_shape], name=f"{prefix}.Shape"))
        nodes.append(
            helper.make_node(
                "Gather",
                [hidden_shape, "constant.index0"],
                [node_count],
                axis=0,
                name=f"{prefix}.NodeCount",
            )
        )
        nodes.append(
            helper.make_node(
                "OneHot",
                [target_index, node_count, "constant.onehot_values"],
                [one_hot],
                axis=-1,
                name=f"{prefix}.OneHotTargets",
            )
        )
        nodes.append(
            helper.make_node(
                "Transpose",
                [one_hot],
                [target_matrix],
                perm=[1, 0],
                name=f"{prefix}.TargetMatrix",
            )
        )
        nodes.append(helper.make_node("MatMul", [target_matrix, messages], [aggregate], name=f"{prefix}.Aggregate"))
        nodes.append(
            helper.make_node(
                "ReduceSum",
                [target_matrix, "constant.axis1"],
                [degree],
                keepdims=1,
                name=f"{prefix}.Degree",
            )
        )
        nodes.append(
            helper.make_node(
                "Max",
                [degree, "constant.minimum_degree"],
                [bounded_degree],
                name=f"{prefix}.BoundDegree",
            )
        )
        nodes.append(
            helper.make_node(
                "Div",
                [aggregate, bounded_degree],
                [mean_aggregate],
                name=f"{prefix}.MeanAggregate",
            )
        )

        self_projection = _linear(nodes, hidden, f"{prefix}.self_projection", f"{prefix}.self")
        combined = f"{prefix}.combined"
        nodes.append(helper.make_node("Add", [self_projection, mean_aggregate], [combined], name=f"{prefix}.Combine"))
        hidden = _gelu(nodes, combined, f"{prefix}.gelu")

    _linear(nodes, hidden, "role_head", "role_logits")

    product_index = _column(nodes, "relation_index", 0, "relation.product_index")
    field_index = _column(nodes, "relation_index", 1, "relation.field_index")
    product_hidden = "relation.product_hidden"
    field_hidden = "relation.field_hidden"
    nodes.append(
        helper.make_node(
            "Gather",
            [hidden, product_index],
            [product_hidden],
            axis=0,
            name="relation.GatherProduct",
        )
    )
    nodes.append(
        helper.make_node(
            "Gather",
            [hidden, field_index],
            [field_hidden],
            axis=0,
            name="relation.GatherField",
        )
    )
    pair_input = "relation.pair_input"
    nodes.append(
        helper.make_node(
            "Concat",
            [product_hidden, field_hidden, "relation_features"],
            [pair_input],
            axis=1,
            name="relation.PairInput",
        )
    )
    pair_projected = _linear(nodes, pair_input, "pair_hidden", "relation.pair_projected")
    pair_hidden = _gelu(nodes, pair_projected, "relation.gelu")
    pair_output = _linear(nodes, pair_hidden, "pair_output", "relation.pair_output")
    nodes.append(
        helper.make_node(
            "Squeeze",
            [pair_output, "constant.axis1"],
            ["relation_logits"],
            name="relation.Squeeze",
        )
    )

    graph = helper.make_graph(
        nodes,
        "medicine_sparse_document_graph_v1",
        [
            helper.make_tensor_value_info("node_features", TensorProto.FLOAT, ["nodes", NODE_FEATURE_DIM]),
            helper.make_tensor_value_info("edge_index", TensorProto.INT64, ["edges", 2]),
            helper.make_tensor_value_info("edge_features", TensorProto.FLOAT, ["edges", EDGE_FEATURE_DIM]),
            helper.make_tensor_value_info("relation_index", TensorProto.INT64, ["relations", 2]),
            helper.make_tensor_value_info("relation_features", TensorProto.FLOAT, ["relations", EDGE_FEATURE_DIM]),
        ],
        [
            helper.make_tensor_value_info("role_logits", TensorProto.FLOAT, ["nodes", len(ROLE_LABELS)]),
            helper.make_tensor_value_info("relation_logits", TensorProto.FLOAT, ["relations"]),
        ],
        initializer=initializers,
    )
    exported = helper.make_model(
        graph,
        producer_name="medicine-graph-export",
        producer_version="1",
        opset_imports=[helper.make_opsetid("", OPSET_VERSION)],
    )
    exported.ir_version = ONNX_IR_VERSION
    onnx.checker.check_model(exported)
    return exported


def _write_onnx(model: paddle.nn.Layer, path: Path) -> None:
    exported = _build_onnx_model(model)
    encoded = exported.SerializeToString(deterministic=True)
    if not encoded:
        raise ValueError("direct ONNX export produced an empty model")
    path.write_bytes(encoded)
    onnx.checker.check_model(onnx.load(str(path)))


def _parity_inputs() -> tuple[dict[str, np.ndarray], ...]:
    first_nodes = np.zeros((3, NODE_FEATURE_DIM), dtype=np.float32)
    first_nodes[0, -1] = 1.0
    first_nodes[1, :4] = [0.2, -0.1, 0.3, 0.4]
    first_nodes[2, :4] = [-0.2, 0.5, 0.1, -0.3]
    first_edges = np.asarray(
        [[0, 1], [1, 0], [0, 2], [2, 0], [1, 2], [2, 1]],
        dtype=np.int64,
    )
    first_edge_features = np.zeros((len(first_edges), EDGE_FEATURE_DIM), dtype=np.float32)
    first_edge_features[:4, -1] = 1.0
    first_relation_features = np.zeros((1, EDGE_FEATURE_DIM), dtype=np.float32)
    first_relation_features[0, :3] = [0.3, 0.0, 0.3]

    second_nodes = np.zeros((5, NODE_FEATURE_DIM), dtype=np.float32)
    second_nodes[0, -1] = 1.0
    second_nodes[1:, :5] = np.asarray(
        [
            [0.1, 0.2, -0.3, 0.4, 0.2],
            [-0.2, 0.4, 0.5, -0.1, 0.3],
            [0.6, -0.2, 0.1, 0.3, -0.4],
            [-0.1, 0.3, -0.2, 0.5, 0.6],
        ],
        dtype=np.float32,
    )
    second_edges = np.asarray(
        [
            [0, 1], [1, 0], [0, 2], [2, 0], [0, 3], [3, 0], [0, 4], [4, 0],
            [1, 2], [2, 3], [3, 4], [4, 1],
        ],
        dtype=np.int64,
    )
    second_edge_features = np.zeros((len(second_edges), EDGE_FEATURE_DIM), dtype=np.float32)
    second_edge_features[:, :2] = np.asarray(
        [[index / 20.0, -index / 30.0] for index in range(len(second_edges))],
        dtype=np.float32,
    )
    second_relation_features = np.zeros((2, EDGE_FEATURE_DIM), dtype=np.float32)
    second_relation_features[0, :3] = [0.1, 0.2, 0.25]
    second_relation_features[1, :3] = [-0.2, 0.4, 0.45]

    return (
        {
            "node_features": first_nodes,
            "edge_index": first_edges,
            "edge_features": first_edge_features,
            "relation_index": np.asarray([[1, 2]], dtype=np.int64),
            "relation_features": first_relation_features,
        },
        {
            "node_features": second_nodes,
            "edge_index": second_edges,
            "edge_features": second_edge_features,
            "relation_index": np.asarray([[1, 2], [3, 4]], dtype=np.int64),
            "relation_features": second_relation_features,
        },
    )


def _maximum_delta(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError(f"ONNX parser parity shape differs: {left.shape} != {right.shape}")
    if left.size == 0:
        return 0.0
    return float(np.max(np.abs(left - right)))


def _parity_check(model: paddle.nn.Layer, onnx_path: Path) -> tuple[dict[str, Any], float, list[dict[str, Any]]]:
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_names = [item.name for item in session.get_inputs()]
    expected_inputs = ["node_features", "edge_index", "edge_features", "relation_index", "relation_features"]
    if input_names != expected_inputs:
        raise ValueError(f"exported parser input names differ from runtime contract: {input_names}")
    output_names = [item.name for item in session.get_outputs()]
    if output_names != ["role_logits", "relation_logits"]:
        raise ValueError(f"exported parser output names differ from runtime contract: {output_names}")

    maximum_delta = 0.0
    cases: list[dict[str, Any]] = []
    for index, inputs in enumerate(_parity_inputs(), start=1):
        onnx_role, onnx_relation = session.run(None, inputs)
        role, relation = model(
            paddle.to_tensor(inputs["node_features"]),
            paddle.to_tensor(inputs["edge_index"]),
            paddle.to_tensor(inputs["edge_features"]),
            paddle.to_tensor(inputs["relation_index"]),
            paddle.to_tensor(inputs["relation_features"]),
        )
        role_delta = _maximum_delta(onnx_role, role.numpy())
        relation_delta = _maximum_delta(onnx_relation, relation.numpy())
        case_delta = max(role_delta, relation_delta)
        maximum_delta = max(maximum_delta, case_delta)
        cases.append(
            {
                "case": index,
                "nodes": int(inputs["node_features"].shape[0]),
                "edges": int(inputs["edge_index"].shape[0]),
                "relations": int(inputs["relation_index"].shape[0]),
                "role_max_abs_delta": role_delta,
                "relation_max_abs_delta": relation_delta,
            }
        )
    if not np.isfinite(maximum_delta) or maximum_delta > PARITY_TOLERANCE:
        raise ValueError(f"ONNX parser parity exceeds tolerance: {maximum_delta}")

    io = {
        "inputs": [
            {
                "name": item.name,
                "type": item.type,
                "shape": [value if isinstance(value, int) else None for value in item.shape],
            }
            for item in session.get_inputs()
        ],
        "outputs": [
            {
                "name": item.name,
                "type": item.type,
                "shape": [value if isinstance(value, int) else None for value in item.shape],
            }
            for item in session.get_outputs()
        ],
    }
    return io, maximum_delta, cases


def _validate_completed(output: Path, expected_source_sha256: str) -> dict[str, Any]:
    manifest_path = output / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ValueError("parser export directory exists without manifest.json")
    manifest = _json_object(manifest_path)
    if manifest.get("status") != "ok" or manifest.get("schema_version") != 1:
        raise ValueError("parser export manifest is not completed successfully")
    if manifest.get("source_model_result_sha256") != expected_source_sha256:
        raise ValueError("parser export source model result differs from requested model")
    if manifest.get("implementation_sha256") != _implementation_sha256():
        raise ValueError("parser export implementation differs from the completed artifact")
    if manifest.get("toolchain") != _toolchain():
        raise ValueError("parser export toolchain differs from the completed artifact")
    model_path = output / str(manifest.get("model_file") or "")
    if not model_path.is_file() or manifest.get("model_sha256") != _sha256_file(model_path):
        raise ValueError("parser export model SHA-256 mismatch")
    onnx.checker.check_model(onnx.load(str(model_path)))
    return manifest


def export_graph_model(*, model_result: str | Path, output_dir: str | Path) -> dict[str, Any]:
    result_path = Path(model_result).resolve()
    if not result_path.is_file():
        raise ValueError(f"parser graph training result does not exist: {result_path}")
    result_sha256 = _sha256_file(result_path)
    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.export.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"parser graph export is already active for {output}") from exc
        if output.exists():
            return _validate_completed(output, result_sha256)

        bundle = load_graph_model(result_path, device="cpu")
        temporary = output.parent / f".{output.name}.tmp-{os.getpid()}"
        shutil.rmtree(temporary, ignore_errors=True)
        temporary.mkdir()
        try:
            final_model = temporary / MODEL_FILE
            _write_onnx(bundle.model, final_model)
            io, maximum_delta, parity_cases = _parity_check(bundle.model, final_model)
            result = _json_object(result_path)
            architecture = result.get("profile", {}).get("architecture")
            if not isinstance(architecture, Mapping):
                raise ValueError("parser graph training result architecture is missing")
            manifest = {
                "schema_version": 1,
                "status": "ok",
                "model_format": "onnx",
                "model_file": MODEL_FILE,
                "model_sha256": _sha256_file(final_model),
                "source_model_result_sha256": result_sha256,
                "source_checkpoint_sha256": bundle.checkpoint_sha256,
                "implementation_sha256": _implementation_sha256(),
                "toolchain": _toolchain(),
                "architecture": dict(architecture),
                "parameter_count": int(result.get("parameter_count") or 0),
                "inputs": io["inputs"],
                "outputs": io["outputs"],
                "parity_tolerance": PARITY_TOLERANCE,
                "parity_max_abs_delta": maximum_delta,
                "parity_cases": parity_cases,
            }
            _atomic_json(temporary / MANIFEST_FILE, manifest)
            os.replace(temporary, output)
            return manifest
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


__all__ = ["export_graph_model"]