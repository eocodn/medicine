from __future__ import annotations

import unittest

import paddle

from browser_ocr.document_parsing.parser_v5_encoder_paddle import (
    ParserV5EncoderSpec,
    ParserV5GlobalEncoder,
    parser_v5_tensors,
)
from browser_ocr.document_parsing.parser_v5_heads_paddle import (
    ParserV5SemanticAssignmentHead,
    parser_v5_head_loss,
    parser_v5_head_targets,
)
from browser_ocr.document_parsing.parser_v5_decode import ParserV5DecodeConfig
from browser_ocr.document_parsing.parser_v5_inference_paddle import run_parser_v5_inference
from browser_ocr.document_parsing.parser_v5_model_input import build_parser_v5_model_input, build_parser_v5_runtime_input
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_structured_targets import build_parser_v5_structured_targets
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV5HeadTest(unittest.TestCase):
    def _pair(self, *, false_positives: int = 0):
        document = generate_parser_world(
            seed=444,
            document_index=7,
            profile=ParserWorldProfile(medication_count=(2, 2), distractor_section_count=(2, 2)),
        )
        observation = simulate_observations(
            document,
            seed=555,
            profile=ObservationProfile(
                text_corruption_rate=0,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=0,
                geometry_jitter=0,
                false_positive_count=(false_positives, false_positives),
                reading_order_shuffle_rate=0,
            ),
        )
        return document, observation

    def test_head_targets_distinguish_real_text_from_detector_false_positive(self) -> None:
        document, observation = self._pair(false_positives=1)
        model_input = build_parser_v5_model_input(document, observation, max_text_bytes=48)
        structured = build_parser_v5_structured_targets(document, observation)
        targets = parser_v5_head_targets(structured, node_count=len(model_input.node_ids))
        self.assertEqual(int((targets.candidate_targets == 0).astype("int64").sum().item()), 1)
        self.assertEqual(int(targets.candidate_mask.sum().item()), len(model_input.node_ids))

    def test_assignment_is_one_softmax_over_product_slots_plus_none(self) -> None:
        paddle.seed(777)
        document, observation = self._pair()
        model_input = build_parser_v5_model_input(document, observation, max_text_bytes=48)
        tensors = parser_v5_tensors(model_input)
        structured = build_parser_v5_structured_targets(document, observation)
        targets = parser_v5_head_targets(structured, node_count=len(model_input.node_ids))
        encoder = ParserV5GlobalEncoder(ParserV5EncoderSpec(hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4))
        head = ParserV5SemanticAssignmentHead(hidden_dim=64, assignment_hidden_dim=48)
        hidden, _ = encoder(tensors)
        candidate_logits, assignment_logits = head(hidden, tensors.relation_features, targets)
        self.assertEqual(list(candidate_logits.shape), [len(model_input.node_ids)])
        self.assertEqual(list(assignment_logits.shape), [len(structured.field_spans), len(structured.product_slots) + 1])
        self.assertTrue(bool(paddle.isfinite(parser_v5_head_loss(candidate_logits, assignment_logits, targets)).item()))

    def test_missing_product_slot_masks_fields_instead_of_relabeling_them_none(self) -> None:
        document, observation = self._pair()
        product = next(node for node in observation["nodes"] if any(t["semantic_role"] == "product" and t["association_group"] == "med-01" for t in node["targets"]))
        observation["nodes"].remove(product)
        model_input = build_parser_v5_model_input(document, observation, max_text_bytes=48)
        structured = build_parser_v5_structured_targets(document, observation)
        targets = parser_v5_head_targets(structured, node_count=len(model_input.node_ids))
        masked = [index for index, field in enumerate(structured.field_spans) if field.association_group == "med-01"]
        self.assertTrue(masked)
        self.assertTrue(all(float(targets.assignment_mask[index].item()) == 0.0 for index in masked))
        none_index = len(structured.product_slots)
        self.assertTrue(all(int(targets.assignment_targets[index].item()) != none_index for index in masked))

    def test_assignment_scorer_accepts_truth_free_predicted_instances(self) -> None:
        paddle.seed(991)
        hidden = paddle.randn([5, 64])
        relation_features = paddle.randn([5, 5, 14])
        head = ParserV5SemanticAssignmentHead(hidden_dim=64, assignment_hidden_dim=48)
        membership = paddle.to_tensor([
            [1.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0, 0.0],
        ], dtype="float32")
        logits = head.score_assignments(
            hidden,
            relation_features,
            product_membership=membership,
            product_available=paddle.to_tensor([True, True], dtype="bool"),
            field_node_index=paddle.to_tensor([2, 3], dtype="int64"),
            field_role_index=paddle.to_tensor([0, 1], dtype="int64"),
        )
        self.assertEqual(list(logits.shape), [2, 3])
        self.assertTrue(bool(paddle.isfinite(logits).all().item()))

    def test_end_to_end_inference_path_consumes_no_truth_targets(self) -> None:
        document, observation = self._pair()
        runtime_nodes = [{
            "node_id": node["node_id"],
            "text": node["text"],
            "detector_confidence": node["detector_confidence"],
            "recognizer_confidence": node["recognizer_confidence"],
            "polygon": node["polygon"],
        } for node in observation["nodes"]]
        model_input = build_parser_v5_runtime_input(
            document_id="runtime-only",
            width=document["width"],
            height=document["height"],
            nodes=runtime_nodes,
            max_text_bytes=48,
        )
        encoder = ParserV5GlobalEncoder(
            ParserV5EncoderSpec(hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4)
        )
        head = ParserV5SemanticAssignmentHead(hidden_dim=64, assignment_hidden_dim=48)
        result = run_parser_v5_inference(
            encoder=encoder,
            heads=head,
            model_input=model_input,
            nodes=runtime_nodes,
            config=ParserV5DecodeConfig(
                candidate_threshold=0.0,
                role_threshold=0.0,
                assignment_threshold=1.0,
                assignment_margin=1.0,
            ),
        )
        self.assertEqual(len(result.role_probabilities), len(runtime_nodes))
        self.assertEqual(len(result.product_node_indices), len(runtime_nodes))
        self.assertEqual(len(result.rows), len(runtime_nodes))


if __name__ == "__main__":
    unittest.main()