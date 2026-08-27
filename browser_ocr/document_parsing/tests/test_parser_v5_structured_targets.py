from __future__ import annotations

import unittest

from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, simulate_observations
from browser_ocr.document_parsing.parser_v5_structured_targets import build_parser_v5_structured_targets
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV5StructuredTargetsTest(unittest.TestCase):
    def _pair(self, medication_count: int = 2):
        document = generate_parser_world(
            seed=222,
            document_index=4,
            profile=ParserWorldProfile(
                medication_count=(medication_count, medication_count),
                distractor_section_count=(2, 2),
            ),
        )
        observation = simulate_observations(
            document,
            seed=333,
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
        return document, observation

    def test_fields_choose_one_product_slot_not_pairwise_edges(self) -> None:
        document, observation = self._pair(2)
        targets = build_parser_v5_structured_targets(document, observation)
        self.assertEqual([slot.medication_id for slot in targets.product_slots], ["med-01", "med-02"])
        self.assertTrue(all(slot.member_node_indices for slot in targets.product_slots))
        supervised = [target for target in targets.field_spans if target.supervised]
        self.assertTrue(supervised)
        self.assertTrue(all(target.target_slot_index in {0, 1} for target in supervised))
        self.assertTrue(all(target.none_target is False for target in supervised))

    def test_merged_multi_role_node_keeps_separate_span_assignments(self) -> None:
        document, observation = self._pair(1)
        dose = next(node for node in observation["nodes"] if node["targets"] and node["targets"][0]["semantic_role"] == "dose")
        frequency = next(
            node for node in observation["nodes"] if node["targets"] and node["targets"][0]["semantic_role"] == "frequency"
        )
        dose["source_span_ids"] = [*dose["source_span_ids"], *frequency["source_span_ids"]]
        dose["targets"] = [*dose["targets"], *frequency["targets"]]
        dose["text"] = f"{frequency['text']} {dose['text']}"
        dose["operation"] = "merge"
        observation["nodes"].remove(frequency)
        targets = build_parser_v5_structured_targets(document, observation)
        same_node = [target for target in targets.field_spans if target.node_id == dose["node_id"]]
        self.assertEqual({target.semantic_role for target in same_node}, {"dose", "frequency"})
        self.assertEqual({target.target_slot_index for target in same_node}, {0})

    def test_missing_product_observation_masks_unassignable_field_instead_of_teaching_none(self) -> None:
        document, observation = self._pair(1)
        product_node = next(
            node for node in observation["nodes"] if any(target["semantic_role"] == "product" for target in node["targets"])
        )
        observation["nodes"].remove(product_node)
        targets = build_parser_v5_structured_targets(document, observation)
        self.assertEqual(targets.product_slots[0].member_node_indices, ())
        medication_fields = [target for target in targets.field_spans if target.association_group == "med-01"]
        self.assertTrue(medication_fields)
        self.assertTrue(all(not target.supervised for target in medication_fields))
        self.assertTrue(all(not target.none_target for target in medication_fields))

    def test_zero_medication_document_has_no_product_or_field_targets(self) -> None:
        document, observation = self._pair(0)
        targets = build_parser_v5_structured_targets(document, observation)
        self.assertEqual(targets.product_slots, ())
        self.assertEqual(targets.field_spans, ())


if __name__ == "__main__":
    unittest.main()