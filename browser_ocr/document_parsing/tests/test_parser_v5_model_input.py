from __future__ import annotations

import unittest

from browser_ocr.document_parsing.parser_v5_model_input import (
    BYTE_EOS,
    BYTE_PAD,
    PARSER_V5_ROLE_LABELS,
    build_parser_v5_model_input,
    encode_text_bytes,
)
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV5ModelInputTest(unittest.TestCase):
    def _world(self):
        return generate_parser_world(
            seed=44,
            document_index=0,
            profile=ParserWorldProfile(medication_count=(1, 1), distractor_section_count=(1, 1)),
        )

    def test_utf8_byte_encoding_preserves_sequence_order(self) -> None:
        forward, forward_mask = encode_text_bytes("가1정", max_bytes=12)
        reverse, reverse_mask = encode_text_bytes("정1가", max_bytes=12)

        self.assertNotEqual(forward, reverse)
        self.assertEqual(sum(forward_mask), sum(reverse_mask))
        self.assertEqual(forward[sum(forward_mask) - 1], BYTE_EOS)
        self.assertTrue(all(token == BYTE_PAD for token in forward[sum(forward_mask) :]))

    def test_model_input_keeps_detector_and_recognizer_confidence_separate(self) -> None:
        truth = self._world()
        observation = simulate_observations(
            truth,
            seed=9,
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
        observation["nodes"][0]["detector_confidence"] = 0.91
        observation["nodes"][0]["recognizer_confidence"] = 0.63
        model_input = build_parser_v5_model_input(truth, observation, max_text_bytes=48)

        self.assertEqual(model_input.node_scalars[0][5], 0.91)
        self.assertEqual(model_input.node_scalars[0][6], 0.63)

    def test_merged_observation_has_multi_label_semantic_supervision(self) -> None:
        truth = self._world()
        observation = simulate_observations(
            truth,
            seed=21,
            profile=ObservationProfile(
                text_corruption_rate=0,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=1,
                geometry_jitter=0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0,
            ),
        )
        model_input = build_parser_v5_model_input(truth, observation, max_text_bytes=64)
        merged_index = next(index for index, node in enumerate(observation["nodes"]) if len(node["targets"]) > 1)
        roles = {
            PARSER_V5_ROLE_LABELS[index]
            for index, value in enumerate(model_input.role_targets[merged_index])
            if value == 1.0
        }

        self.assertGreaterEqual(len(roles), 2)
        self.assertIn("dose", roles)
        self.assertIn("frequency", roles)

    def test_relation_features_are_dense_global_and_directional(self) -> None:
        truth = self._world()
        observation = simulate_observations(
            truth,
            seed=3,
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
        model_input = build_parser_v5_model_input(truth, observation, max_text_bytes=48)
        count = len(observation["nodes"])

        self.assertEqual(len(model_input.relation_features), count)
        self.assertTrue(all(len(row) == count for row in model_input.relation_features))
        self.assertEqual(model_input.relation_features[0][0][-1], 1.0)
        self.assertAlmostEqual(model_input.relation_features[0][1][0], -model_input.relation_features[1][0][0])
        self.assertAlmostEqual(model_input.relation_features[0][1][1], -model_input.relation_features[1][0][1])


if __name__ == "__main__":
    unittest.main()