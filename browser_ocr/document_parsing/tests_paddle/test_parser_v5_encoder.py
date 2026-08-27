from __future__ import annotations

import unittest

import paddle

from browser_ocr.document_parsing.parser_v5_model_input import build_parser_v5_model_input
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world
from browser_ocr.document_parsing.parser_v5_encoder_paddle import (
    ParserV5EncoderSpec,
    ParserV5GlobalEncoder,
    model_parameter_count,
    parser_v5_tensors,
)


class ParserV5EncoderTest(unittest.TestCase):
    def _input(self):
        document = generate_parser_world(
            seed=77,
            document_index=2,
            profile=ParserWorldProfile(medication_count=(2, 2), distractor_section_count=(2, 2)),
        )
        observation = simulate_observations(
            document,
            seed=88,
            profile=ObservationProfile(
                text_corruption_rate=0,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=0,
                geometry_jitter=0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0,
            ),
        )
        return build_parser_v5_model_input(document, observation, max_text_bytes=48)

    def test_tensorization_preserves_dense_relation_shape_and_masks(self) -> None:
        model_input = self._input()
        tensors = parser_v5_tensors(model_input)
        node_count = len(model_input.node_ids)
        self.assertEqual(list(tensors.token_ids.shape), [node_count, 48])
        self.assertEqual(list(tensors.relation_features.shape), [node_count, node_count, 14])
        self.assertEqual(list(tensors.role_targets.shape), [node_count, 9])
        self.assertEqual(list(tensors.role_mask.shape), [node_count, 9])

    def test_sequence_encoder_is_order_sensitive(self) -> None:
        paddle.seed(123)
        spec = ParserV5EncoderSpec(hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4)
        model = ParserV5GlobalEncoder(spec)
        model.eval()
        first = self._input()
        reversed_tokens = tuple(tuple(reversed(row)) for row in first.token_ids)
        mutated = first.__class__(
            document_id=first.document_id,
            node_ids=first.node_ids,
            token_ids=reversed_tokens,
            token_mask=first.token_mask,
            node_scalars=first.node_scalars,
            relation_features=first.relation_features,
            role_targets=first.role_targets,
            role_mask=first.role_mask,
        )
        a = parser_v5_tensors(first)
        b = parser_v5_tensors(mutated)
        with paddle.no_grad():
            hidden_a, _ = model(a)
            hidden_b, _ = model(b)
        self.assertFalse(bool(paddle.allclose(hidden_a, hidden_b).item()))

    def test_global_relation_change_can_change_distant_node_state(self) -> None:
        paddle.seed(456)
        model_input = self._input()
        tensors = parser_v5_tensors(model_input)
        spec = ParserV5EncoderSpec(hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=2, heads=4)
        model = ParserV5GlobalEncoder(spec)
        model.eval()
        changed_relation = tensors.relation_features.clone()
        changed_relation[0, -1, 0] = changed_relation[0, -1, 0] + 0.75
        changed = tensors.__class__(
            token_ids=tensors.token_ids,
            token_mask=tensors.token_mask,
            node_scalars=tensors.node_scalars,
            relation_features=changed_relation,
            role_targets=tensors.role_targets,
            role_mask=tensors.role_mask,
        )
        with paddle.no_grad():
            hidden_a, _ = model(tensors)
            hidden_b, _ = model(changed)
        self.assertFalse(bool(paddle.allclose(hidden_a[-1], hidden_b[-1]).item()))

    def test_encoder_emits_multilabel_role_logits_and_stays_mobile_sized(self) -> None:
        tensors = parser_v5_tensors(self._input())
        spec = ParserV5EncoderSpec(hidden_dim=96, text_embedding_dim=32, text_conv_dim=48, layers=2, heads=4)
        model = ParserV5GlobalEncoder(spec)
        hidden, role_logits = model(tensors)
        self.assertEqual(list(hidden.shape), [len(self._input().node_ids), 96])
        self.assertEqual(list(role_logits.shape), [len(self._input().node_ids), 9])
        self.assertLess(model_parameter_count(model), 1_000_000)


if __name__ == "__main__":
    unittest.main()