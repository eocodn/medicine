from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import paddle.nn as nn

from .parser_v5_document_encoder_paddle import (
    ParserV5DocumentEncoder,
    ParserV5EncoderSpec,
    parser_v5_document_tensors,
)
from .parser_v5_model_input import build_parser_v5_runtime_document_input
from .parser_v51_direct_decoder_paddle import (
    ParserV51DecoderOutput,
    ParserV51DecoderSpec,
    ParserV51DirectRowDecoder,
)
from .parser_v51_targets import ParserV51RowTargets, build_parser_v51_row_targets


@dataclass(frozen=True)
class ParserV51ModelConfig:
    max_text_bytes: int = 96
    hidden_dim: int = 96
    text_embedding_dim: int = 32
    text_conv_dim: int = 48
    layers: int = 2
    heads: int = 4
    feedforward_multiplier: int = 2
    max_rows: int = 8
    max_field_pieces: int = 2

    def __post_init__(self) -> None:
        if not 4 <= self.max_text_bytes <= 512:
            raise ValueError("Parser v5.1 max_text_bytes must be between 4 and 512")
        self.encoder_spec
        self.decoder_spec

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

    @property
    def decoder_spec(self) -> ParserV51DecoderSpec:
        return ParserV51DecoderSpec(
            hidden_dim=self.hidden_dim,
            text_token_dim=self.text_conv_dim * 2,
            max_rows=self.max_rows,
            max_field_pieces=self.max_field_pieces,
            feedforward_multiplier=self.feedforward_multiplier,
        )


class ParserV51Model(nn.Layer):
    def __init__(self, config: ParserV51ModelConfig = ParserV51ModelConfig()) -> None:
        super().__init__()
        self.config = config
        self.encoder = ParserV5DocumentEncoder(config.encoder_spec)
        self.decoder = ParserV51DirectRowDecoder(config.decoder_spec)

    def forward(self, tensors) -> ParserV51DecoderOutput:
        node_hidden, token_states = self.encoder(tensors)
        return self.decoder(node_hidden, token_states, tensors)


def prepare_parser_v51_sample(
    sample: Mapping[str, Any],
    config: ParserV51ModelConfig,
):
    truth = sample["truth"]
    observation = sample["observation"]
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
    document_input = build_parser_v5_runtime_document_input(
        document_id=str(truth["document_id"]),
        width=truth["width"],
        height=truth["height"],
        nodes=nodes,
        max_text_bytes=config.max_text_bytes,
    )
    if len(document_input.node_ids) != len(nodes):
        raise ValueError("Parser v5.1 synthetic training observations must not contain blank OCR nodes")
    tensors = parser_v5_document_tensors(document_input)
    targets: ParserV51RowTargets = build_parser_v51_row_targets(truth, observation)
    return tensors, targets, tuple(nodes)


__all__ = ["ParserV51Model", "ParserV51ModelConfig", "prepare_parser_v51_sample"]