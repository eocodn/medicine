from __future__ import annotations

import unittest

import paddle

from browser_ocr.document_parsing.parser_v5_document_encoder_paddle import (
    ParserV5DocumentEncoder,
    ParserV5EncoderSpec,
    parser_v5_document_tensors,
)
from browser_ocr.document_parsing.parser_v5_model_input import build_parser_v5_runtime_document_input
from browser_ocr.document_parsing.parser_v51_direct_decoder_paddle import (
    ParserV51DecoderOutput,
    ParserV51DecoderSpec,
    ParserV51DirectRowDecoder,
    decode_parser_v51_rows,
    field_node_pointer_logits,
    field_span_pointer_logits,
    scaled_pointer_scores,
)
from browser_ocr.document_parsing.parser_v51_loss_paddle import match_parser_v51_rows, parser_v51_set_loss
from browser_ocr.document_parsing.parser_v51_model_paddle import (
    ParserV51Model,
    ParserV51ModelConfig,
    prepare_parser_v51_sample,
)
from browser_ocr.document_parsing.parser_v51_targets import (
    ROW_FIELD_ROLES,
    ParserV51FieldTarget,
    ParserV51MedicationRowTarget,
    ParserV51RowTargets,
    ParserV51SpanPieceTarget,
)
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV51DirectDecoderTest(unittest.TestCase):
    def test_cross_attention_values_preserve_node_state_geometry(self) -> None:
        decoder = ParserV51DirectRowDecoder(ParserV51DecoderSpec(hidden_dim=32, max_rows=4))
        node_hidden = paddle.to_tensor(
            [
                [1.0] + [0.0] * 31,
                [0.0, 1.0] + [0.0] * 30,
                [1.0, 1.0] + [0.0] * 30,
            ],
            dtype="float32",
        )

        values = decoder._cross_attention_values(node_hidden)

        self.assertTrue(bool(paddle.allclose(values, node_hidden).item()))

    def test_pointer_scores_use_hidden_dimension_scaling(self) -> None:
        query = paddle.ones([1, 64], dtype="float32")
        key = paddle.ones([1, 64], dtype="float32")
        score = scaled_pointer_scores(query, key, hidden_dim=64)
        self.assertAlmostEqual(float(score.item()), 8.0, places=6)

    def _nodes(self) -> list[dict]:
        return [
            {
                "node_id": "product",
                "text": "약A",
                "detector_confidence": 0.9,
                "recognizer_confidence": 0.96,
                "polygon": [[50, 100], [200, 100], [200, 140], [50, 140]],
            },
            {
                "node_id": "dose",
                "text": "1정",
                "detector_confidence": 0.88,
                "recognizer_confidence": 0.94,
                "polygon": [[220, 100], [300, 100], [300, 140], [220, 140]],
            },
            {
                "node_id": "unused",
                "text": "약제비 계산서 영수증",
                "detector_confidence": 0.95,
                "recognizer_confidence": 0.99,
                "polygon": [[50, 300], [450, 300], [450, 340], [50, 340]],
            },
        ]

    def _empty_output(self, *, rows: int, node_count: int, text_length: int) -> ParserV51DecoderOutput:
        fields = len(ROW_FIELD_ROLES)
        hidden = rows * fields + 1
        existence = paddle.full([rows], -10.0, dtype="float32")
        field_states = paddle.zeros([rows, fields, hidden], dtype="float32")
        for row_index in range(rows):
            for field_index in range(fields):
                field_states[row_index, field_index, row_index * fields + field_index] = 1.0
        node_keys = paddle.zeros([node_count + 1, hidden], dtype="float32")
        stop_dim = rows * fields
        node_keys[node_count, :stop_dim] = 4.0
        node_keys[node_count, stop_dim] = 8.0
        start_keys = paddle.zeros([node_count, text_length, hidden], dtype="float32")
        end_keys = paddle.zeros([node_count, text_length, hidden], dtype="float32")
        values = paddle.zeros([node_count, text_length, hidden], dtype="float32")
        valid = paddle.ones([node_count, text_length], dtype="bool")
        return ParserV51DecoderOutput(
            row_existence_logits=existence,
            field_query_states=field_states,
            node_pointer_keys=node_keys,
            start_pointer_keys=start_keys,
            end_pointer_keys=end_keys,
            evidence_values=values,
            token_valid_mask=valid,
        )

    @staticmethod
    def _script_single_span(
        output: ParserV51DecoderOutput,
        *,
        row: int,
        role: str,
        node: int,
        start_byte: int,
        end_byte: int,
        score: float = 10.0,
    ) -> None:
        field = ROW_FIELD_ROLES.index(role)
        field_dim = row * len(ROW_FIELD_ROLES) + field
        start_token = start_byte + 1
        end_token = end_byte
        stop_dim = output.field_query_states.shape[-1] - 1
        output.node_pointer_keys[node, field_dim] = score
        output.start_pointer_keys[node, start_token, field_dim] = score
        output.end_pointer_keys[node, end_token, field_dim] = score
        transition = paddle.zeros([output.field_query_states.shape[-1]], dtype="float32")
        transition[stop_dim] = 1.0
        transition[field_dim] = -1.0
        output.evidence_values[node, start_token : end_token + 1] = transition

    def test_direct_decoder_emits_autoregressive_evidence_memory(self) -> None:
        paddle.seed(51)
        nodes = self._nodes()
        value = build_parser_v5_runtime_document_input(
            document_id="direct", width=1000, height=1400, nodes=nodes, max_text_bytes=32
        )
        tensors = parser_v5_document_tensors(value)
        encoder = ParserV5DocumentEncoder(
            ParserV5EncoderSpec(hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4)
        )
        decoder = ParserV51DirectRowDecoder(ParserV51DecoderSpec(hidden_dim=64, text_token_dim=64, max_rows=5))
        node_hidden, token_states = encoder(tensors)
        output = decoder(node_hidden, token_states, tensors)

        self.assertEqual(list(output.row_existence_logits.shape), [5])
        self.assertEqual(list(output.field_query_states.shape), [5, len(ROW_FIELD_ROLES), 64])
        self.assertEqual(list(output.node_pointer_keys.shape), [4, 64])
        self.assertEqual(list(output.start_pointer_keys.shape), [3, 32, 64])
        self.assertEqual(list(output.end_pointer_keys.shape), [3, 32, 64])
        self.assertEqual(list(output.evidence_values.shape), [3, 32, 64])
        self.assertEqual(list(output.token_valid_mask.shape), [3, 32])
        state = output.field_query_states[0, 0]
        self.assertEqual(list(field_node_pointer_logits(output, state).shape), [4])
        start, end = field_span_pointer_logits(output, state, 0)
        self.assertEqual(list(start.shape), [32])
        self.assertEqual(list(end.shape), [32])
        self.assertFalse(hasattr(output, "field_token_logits"))
        self.assertFalse(hasattr(output, "role_logits"))
        self.assertFalse(hasattr(output, "candidate_logits"))

    def test_decode_uses_only_selected_evidence_and_drops_unrelated_text(self) -> None:
        nodes = self._nodes()
        output = self._empty_output(rows=3, node_count=len(nodes), text_length=32)
        output.row_existence_logits[0] = 10.0
        self._script_single_span(output, row=0, role="product", node=0, start_byte=0, end_byte=len("약A".encode()))
        self._script_single_span(output, row=0, role="dose", node=1, start_byte=0, end_byte=len("1정".encode()))

        decoded = decode_parser_v51_rows(nodes=nodes, output=output)
        self.assertEqual(decoded[0]["product_query"], "약A")
        self.assertEqual(decoded[0]["fields"]["dose"]["text"], "1정")
        self.assertNotIn("영수증", str(decoded[0]))

    def _row_target(self, medication_id: str, pieces: tuple[ParserV51SpanPieceTarget, ...]) -> ParserV51MedicationRowTarget:
        return ParserV51MedicationRowTarget(
            medication_id=medication_id,
            fields=tuple(
                ParserV51FieldTarget(semantic_role=role, pieces=pieces if role == "product" else ())
                for role in ROW_FIELD_ROLES
            ),
        )

    @staticmethod
    def _piece(node: int, start: int, end: int, *, operation: str = "split") -> ParserV51SpanPieceTarget:
        return ParserV51SpanPieceTarget(
            node_index=node,
            node_id=f"n{node}",
            source_span_id="source",
            operation=operation,
            start_char=start,
            end_char=end,
            start_byte=start,
            end_byte=end,
        )

    def test_set_matching_is_row_slot_permutation_invariant(self) -> None:
        output = self._empty_output(rows=3, node_count=2, text_length=8)
        output.row_existence_logits[0] = 8.0
        output.row_existence_logits[2] = 8.0
        self._script_single_span(output, row=0, role="product", node=1, start_byte=0, end_byte=2, score=12.0)
        self._script_single_span(output, row=2, role="product", node=0, start_byte=0, end_byte=2, score=12.0)
        targets = ParserV51RowTargets(
            rows=(self._row_target("A", (self._piece(0, 0, 2),)), self._row_target("B", (self._piece(1, 0, 2),)))
        )
        assignments = match_parser_v51_rows(output, targets)
        self.assertEqual(assignments, ((2, 0), (0, 1)))
        self.assertTrue(bool(paddle.isfinite(parser_v51_set_loss(output, targets)).item()))

    def test_set_loss_supports_more_than_two_disjoint_fragments(self) -> None:
        output = self._empty_output(rows=1, node_count=1, text_length=10)
        output.row_existence_logits[0] = 8.0
        pieces = tuple(self._piece(0, start, start + 1) for start in (0, 2, 4))
        target = self._row_target("A", pieces)
        self.assertTrue(bool(paddle.isfinite(parser_v51_set_loss(output, ParserV51RowTargets(rows=(target,)))).item()))

    def test_direct_model_backpropagates_without_role_or_candidate_supervision(self) -> None:
        paddle.seed(5101)
        truth = generate_parser_world(
            seed=5101,
            document_index=0,
            profile=ParserWorldProfile(medication_count=(2, 2), distractor_section_count=(2, 2)),
        )
        observation = simulate_observations(
            truth,
            seed=5102,
            profile=ObservationProfile(
                text_corruption_rate=0.1,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=0.5,
                geometry_jitter=0.002,
                false_positive_count=(1, 1),
                reading_order_shuffle_rate=0,
            ),
        )
        config = ParserV51ModelConfig(
            max_text_bytes=48,
            hidden_dim=64,
            text_embedding_dim=24,
            text_conv_dim=32,
            layers=1,
            heads=4,
            max_rows=4,
        )
        tensors, targets, _ = prepare_parser_v51_sample({"truth": truth, "observation": observation}, config)
        model = ParserV51Model(config)
        loss = parser_v51_set_loss(model(tensors), targets)

        self.assertTrue(bool(paddle.isfinite(loss).item()))
        loss.backward()
        self.assertGreater(float(paddle.abs(model.decoder.row_queries.grad).sum().item()), 0.0)
        self.assertGreater(float(paddle.abs(model.decoder.row_self_query.weight.grad).sum().item()), 0.0)
        self.assertGreater(float(paddle.abs(model.decoder.node_pointer_key.weight.grad).sum().item()), 0.0)
        self.assertGreater(float(paddle.abs(model.encoder.text_encoder.embedding.weight.grad).sum().item()), 0.0)


if __name__ == "__main__":
    unittest.main()
