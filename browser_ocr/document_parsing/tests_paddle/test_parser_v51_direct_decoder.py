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

    def _empty_output(
        self,
        *,
        rows: int,
        node_count: int,
        text_length: int,
        pieces: int = 4,
    ) -> ParserV51DecoderOutput:
        fields = len(ROW_FIELD_ROLES)
        existence = paddle.full([rows], -10.0, dtype="float32")
        start_logits = paddle.full([rows, fields, pieces, node_count, text_length], -10.0, dtype="float32")
        end_logits = paddle.full([rows, fields, pieces, node_count, text_length], -10.0, dtype="float32")
        none_logits = paddle.full([rows, fields, pieces, 2], 10.0, dtype="float32")
        return ParserV51DecoderOutput(
            row_existence_logits=existence,
            piece_start_logits=start_logits,
            piece_end_logits=end_logits,
            piece_none_logits=none_logits,
        )

    @staticmethod
    def _select_piece(
        output: ParserV51DecoderOutput,
        *,
        row: int,
        role: str,
        piece: int,
        node: int,
        start_token: int,
        end_token: int,
        score: float = 10.0,
    ) -> None:
        field = ROW_FIELD_ROLES.index(role)
        output.piece_none_logits[row, field, piece, :] = -score
        output.piece_start_logits[row, field, piece, node, start_token] = score
        output.piece_end_logits[row, field, piece, node, end_token] = score

    def test_direct_decoder_emits_piece_pointer_tensors_without_role_logits(self) -> None:
        paddle.seed(51)
        nodes = self._nodes()
        value = build_parser_v5_runtime_document_input(
            document_id="direct", width=1000, height=1400, nodes=nodes, max_text_bytes=32
        )
        tensors = parser_v5_document_tensors(value)
        encoder = ParserV5DocumentEncoder(
            ParserV5EncoderSpec(
                hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4
            )
        )
        decoder = ParserV51DirectRowDecoder(
            ParserV51DecoderSpec(hidden_dim=64, text_token_dim=64, max_rows=5, max_field_pieces=4)
        )
        node_hidden, token_states = encoder(tensors)
        output = decoder(node_hidden, token_states, tensors)

        self.assertEqual(list(output.row_existence_logits.shape), [5])
        self.assertEqual(list(output.piece_start_logits.shape), [5, len(ROW_FIELD_ROLES), 4, 3, 32])
        self.assertEqual(list(output.piece_end_logits.shape), [5, len(ROW_FIELD_ROLES), 4, 3, 32])
        self.assertEqual(list(output.piece_none_logits.shape), [5, len(ROW_FIELD_ROLES), 4, 2])
        self.assertFalse(hasattr(output, "role_logits"))
        self.assertFalse(hasattr(output, "candidate_logits"))
        self.assertFalse(hasattr(output, "field_node_logits"))

    def test_decode_uses_only_selected_evidence_and_drops_unrelated_text(self) -> None:
        nodes = self._nodes()
        output = self._empty_output(rows=3, node_count=len(nodes), text_length=16)
        output.row_existence_logits[0] = 10.0
        self._select_piece(
            output, row=0, role="product", piece=0, node=0, start_token=1, end_token=len("약A".encode("utf-8"))
        )
        self._select_piece(
            output, row=0, role="dose", piece=0, node=1, start_token=1, end_token=len("1정".encode("utf-8"))
        )

        decoded = decode_parser_v51_rows(nodes=nodes, output=output)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["product_query"], "약A")
        self.assertEqual(decoded[0]["fields"]["dose"]["text"], "1정")
        self.assertNotIn("unused", str(decoded[0]))
        self.assertNotIn("영수증", str(decoded[0]))

    def test_decode_can_reconstruct_one_field_from_multiple_ocr_nodes(self) -> None:
        nodes = [
            {"node_id": "p1", "text": "세티", "detector_confidence": 0.9, "recognizer_confidence": 0.95,
             "polygon": [[10, 10], [80, 10], [80, 40], [10, 40]]},
            {"node_id": "p2", "text": "리진정", "detector_confidence": 0.9, "recognizer_confidence": 0.95,
             "polygon": [[82, 10], [170, 10], [170, 40], [82, 40]]},
        ]
        output = self._empty_output(rows=2, node_count=2, text_length=16)
        output.row_existence_logits[0] = 10.0
        for piece, (node, text) in enumerate(((0, "세티"), (1, "리진정"))):
            self._select_piece(
                output, row=0, role="product", piece=piece, node=node, start_token=1, end_token=len(text.encode("utf-8"))
            )

        decoded = decode_parser_v51_rows(nodes=nodes, output=output)
        self.assertEqual(decoded[0]["product_query"], "세티리진정")
        self.assertEqual([item["node_id"] for item in decoded[0]["product_evidence"]], ["p1", "p2"])

    def _row_target(self, medication_id: str, pieces: tuple[ParserV51SpanPieceTarget, ...]) -> ParserV51MedicationRowTarget:
        return ParserV51MedicationRowTarget(
            medication_id=medication_id,
            fields=tuple(
                ParserV51FieldTarget(semantic_role=role, pieces=pieces if role == "product" else ())
                for role in ROW_FIELD_ROLES
            ),
        )

    def test_set_matching_is_row_slot_permutation_invariant(self) -> None:
        output = self._empty_output(rows=3, node_count=2, text_length=8)
        output.row_existence_logits[0] = 8.0
        output.row_existence_logits[2] = 8.0
        for row_index, node_index in ((0, 1), (2, 0)):
            self._select_piece(output, row=row_index, role="product", piece=0, node=node_index, start_token=1, end_token=2, score=8.0)

        def piece(node_index: int) -> ParserV51SpanPieceTarget:
            return ParserV51SpanPieceTarget(
                node_index=node_index, node_id=f"n{node_index}", source_span_id=f"s{node_index}",
                operation="identity", start_char=0, end_char=2, start_byte=0, end_byte=2
            )

        targets = ParserV51RowTargets(rows=(self._row_target("A", (piece(0),)), self._row_target("B", (piece(1),))))
        assignments = match_parser_v51_rows(output, targets)
        loss = parser_v51_set_loss(output, targets)
        self.assertEqual(assignments, ((2, 0), (0, 1)))
        self.assertLess(float(loss.item()), 0.01)

    def test_set_loss_supervises_all_required_split_product_pieces(self) -> None:
        output = self._empty_output(rows=2, node_count=2, text_length=8)
        output.row_existence_logits[0] = 8.0
        pieces = []
        for piece_index, node_index in enumerate(range(2)):
            self._select_piece(output, row=0, role="product", piece=piece_index, node=node_index, start_token=1, end_token=2, score=8.0)
            pieces.append(
                ParserV51SpanPieceTarget(
                    node_index=node_index, node_id=f"n{node_index}", source_span_id="product-source",
                    operation="split", start_char=0, end_char=2, start_byte=0, end_byte=2
                )
            )
        target = self._row_target("A", tuple(pieces))
        loss = parser_v51_set_loss(output, ParserV51RowTargets(rows=(target,)))
        self.assertLess(float(loss.item()), 0.01)

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
        tensors, targets, _ = prepare_parser_v51_sample(
            {"truth": truth, "observation": observation},
            config,
        )
        model = ParserV51Model(config)
        output = model(tensors)
        loss = parser_v51_set_loss(output, targets)

        self.assertTrue(bool(paddle.isfinite(loss).item()))
        loss.backward()
        self.assertIsNotNone(model.decoder.row_queries.grad)
        self.assertGreater(float(paddle.abs(model.decoder.row_queries.grad).sum().item()), 0.0)
        self.assertIsNotNone(model.encoder.text_encoder.embedding.weight.grad)
        self.assertGreater(float(paddle.abs(model.encoder.text_encoder.embedding.weight.grad).sum().item()), 0.0)


if __name__ == "__main__":
    unittest.main()