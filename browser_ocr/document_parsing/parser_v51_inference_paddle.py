from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import paddle

from .parser_v5_document_encoder_paddle import parser_v5_document_tensors
from .parser_v5_model_input import build_parser_v5_runtime_document_input
from .parser_v51_model_paddle import ParserV51Model, ParserV51ModelConfig
from .parser_v51_runtime_decode import (
    ParserV51RuntimeDecodeConfig,
    ParserV51RuntimeMemory,
    decode_parser_v51_memory,
)


@dataclass(frozen=True)
class ParserV51InferenceResult:
    rows: tuple[dict[str, Any], ...]
    node_ids: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_result(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5.1 training result {path}") from exc
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise ValueError("Parser v5.1 training result is not completed")
    return result


def _model_config(result: Mapping[str, Any]) -> ParserV51ModelConfig:
    profile = result.get("profile")
    if not isinstance(profile, Mapping) or profile.get("model_id") != "parser_v51_direct_rows_v1":
        raise ValueError("Parser v5.1 training result model identity is invalid")
    raw = profile.get("config")
    if not isinstance(raw, Mapping):
        raise ValueError("Parser v5.1 training result config is missing")
    fields = ParserV51ModelConfig.__dataclass_fields__
    values = {name: raw[name] for name in fields if name in raw}
    if set(values) != set(fields):
        raise ValueError("Parser v5.1 training result model config is incomplete")
    return ParserV51ModelConfig(**values)


def _verify_implementation_identity(result: Mapping[str, Any]) -> None:
    profile = result.get("profile")
    expected = profile.get("implementation_sha256") if isinstance(profile, Mapping) else None
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError("Parser v5.1 training result implementation identity is missing")
    root = Path(__file__).resolve().parent
    for raw_name, raw_sha in expected.items():
        name = str(raw_name)
        source = root / name
        if not source.is_file() or _sha256_file(source) != str(raw_sha):
            raise ValueError(f"Parser v5.1 training implementation drifted: {name}")


def load_trained_parser_v51_model(
    training_result: str | Path,
    *,
    device: str = "cpu",
) -> tuple[ParserV51Model, ParserV51ModelConfig]:
    """Load a completed training artifact only under its recorded semantics."""

    result_path = Path(training_result).resolve()
    result = _read_result(result_path)
    _verify_implementation_identity(result)
    checkpoint_value = result.get("best_checkpoint")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        raise ValueError("Parser v5.1 training result best checkpoint is missing")
    checkpoint = (result_path.parent / checkpoint_value).resolve()
    if checkpoint.parent.parent != (result_path.parent / "checkpoints").resolve():
        raise ValueError("Parser v5.1 training checkpoint path escapes artifact root")
    expected_sha = result.get("best_checkpoint_sha256")
    if not checkpoint.is_file() or not isinstance(expected_sha, str) or _sha256_file(checkpoint) != expected_sha:
        raise ValueError("Parser v5.1 training checkpoint identity mismatch")
    if device not in {"cpu", "gpu"}:
        raise ValueError("Parser v5.1 inference device must be cpu or gpu")
    paddle.set_device(device)
    config = _model_config(result)
    model = ParserV51Model(config)
    model.set_state_dict(paddle.load(str(checkpoint)))
    model.eval()
    return model, config


def runtime_memory_from_paddle(output) -> ParserV51RuntimeMemory:
    return ParserV51RuntimeMemory(
        row_existence_logits=np.asarray(output.row_existence_logits.numpy()),
        field_query_states=np.asarray(output.field_query_states.numpy()),
        node_pointer_keys=np.asarray(output.node_pointer_keys.numpy()),
        start_pointer_keys=np.asarray(output.start_pointer_keys.numpy()),
        end_pointer_keys=np.asarray(output.end_pointer_keys.numpy()),
        evidence_values=np.asarray(output.evidence_values.numpy()),
        token_valid_mask=np.asarray(output.token_valid_mask.numpy(), dtype=bool),
    )


@paddle.no_grad()
def run_parser_v51_inference(
    *,
    model: ParserV51Model,
    config: ParserV51ModelConfig,
    document_id: str,
    width: int | float,
    height: int | float,
    nodes: Sequence[Mapping[str, Any]],
    decode_config: ParserV51RuntimeDecodeConfig = ParserV51RuntimeDecodeConfig(),
) -> ParserV51InferenceResult:
    document_input = build_parser_v5_runtime_document_input(
        document_id=document_id,
        width=width,
        height=height,
        nodes=nodes,
        max_text_bytes=config.max_text_bytes,
    )
    by_id = {str(node["node_id"]): node for node in nodes}
    canonical_nodes = tuple(by_id[node_id] for node_id in document_input.node_ids)
    tensors = parser_v5_document_tensors(document_input)
    memory = runtime_memory_from_paddle(model(tensors))
    rows = decode_parser_v51_memory(nodes=canonical_nodes, memory=memory, config=decode_config)
    return ParserV51InferenceResult(rows=tuple(rows), node_ids=document_input.node_ids)


__all__ = [
    "ParserV51InferenceResult",
    "load_trained_parser_v51_model",
    "run_parser_v51_inference",
    "runtime_memory_from_paddle",
]