from __future__ import annotations

import copy
import random
import unittest

from browser_ocr.document_parsing.parser_v5_contract import validate_parser_v5_document
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile, _corrupt_text, simulate_observations
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile, generate_parser_world


class ParserV5WorldTest(unittest.TestCase):
    def test_vocabulary_partitions_are_disjoint_for_product_names_and_wording(self) -> None:
        train = generate_parser_world(
            seed=731,
            document_index=1,
            profile=ParserWorldProfile(
                medication_count=(5, 5),
                distractor_section_count=(0, 0),
                product_vocabulary="train",
                wording_vocabulary="train",
            ),
        )
        unseen = generate_parser_world(
            seed=731,
            document_index=1,
            profile=ParserWorldProfile(
                medication_count=(5, 5),
                distractor_section_count=(0, 0),
                product_vocabulary="unseen",
                wording_vocabulary="unseen",
            ),
        )

        train_products = {item["product_name"] for item in train["medications"]}
        unseen_products = {item["product_name"] for item in unseen["medications"]}
        self.assertTrue(train_products)
        self.assertTrue(unseen_products)
        self.assertTrue(train_products.isdisjoint(unseen_products))
        train_non_product = {
            span["text"] for span in train["spans"] if span["semantic_role"] != "product"
        }
        unseen_non_product = {
            span["text"] for span in unseen["spans"] if span["semantic_role"] != "product"
        }
        self.assertTrue(train_non_product.isdisjoint(unseen_non_product))

    def test_product_training_vocabulary_is_broad_enough_to_resist_name_memorization(self) -> None:
        train_products: set[str] = set()
        unseen_products: set[str] = set()
        for document_index in range(24):
            train = generate_parser_world(
                seed=980,
                document_index=document_index,
                profile=ParserWorldProfile(
                    medication_count=(5, 5),
                    distractor_section_count=(0, 0),
                    product_vocabulary="train",
                ),
            )
            unseen = generate_parser_world(
                seed=981,
                document_index=document_index,
                profile=ParserWorldProfile(
                    medication_count=(5, 5),
                    distractor_section_count=(0, 0),
                    product_vocabulary="unseen",
                ),
            )
            train_products.update(item["product_name"] for item in train["medications"])
            unseen_products.update(item["product_name"] for item in unseen["medications"])

        self.assertGreaterEqual(len(train_products), 30)
        self.assertGreaterEqual(len(unseen_products), 18)
        self.assertTrue(train_products.isdisjoint(unseen_products))

    def test_world_generation_is_deterministic_and_has_no_layout_identity_shortcut(self) -> None:
        profile = ParserWorldProfile(
            medication_count=(2, 2),
            distractor_section_count=(3, 3),
        )
        first = generate_parser_world(seed=624, document_index=7, profile=profile)
        second = generate_parser_world(seed=624, document_index=7, profile=profile)
        different = generate_parser_world(seed=625, document_index=7, profile=profile)

        self.assertEqual(first, second)
        self.assertNotEqual(first, different)
        self.assertNotIn("layout_family", first)
        self.assertNotIn("template_id", first)
        self.assertNotIn("layout_id", first)
        validate_parser_v5_document(first)

    def test_zero_medication_documents_are_first_class_worlds(self) -> None:
        document = generate_parser_world(
            seed=11,
            document_index=3,
            profile=ParserWorldProfile(
                medication_count=(0, 0),
                distractor_section_count=(5, 5),
            ),
        )

        self.assertEqual(document["medications"], [])
        self.assertGreaterEqual(len(document["sections"]), 5)
        self.assertTrue(all(span["association_group"] is None for span in document["spans"]))
        validate_parser_v5_document(document)

    def test_many_medication_high_distractor_worlds_always_fit_page(self) -> None:
        profile = ParserWorldProfile(
            medication_count=(7, 7),
            distractor_section_count=(10, 12),
        )
        for seed in range(40):
            document = generate_parser_world(seed=seed, document_index=seed, profile=profile)
            validate_parser_v5_document(document)

    def test_semantic_truth_exposes_span_level_roles_and_medication_groups(self) -> None:
        document = generate_parser_world(
            seed=29,
            document_index=1,
            profile=ParserWorldProfile(
                medication_count=(2, 2),
                distractor_section_count=(0, 0),
            ),
        )

        medication_groups = {item["medication_id"] for item in document["medications"]}
        product_spans = [span for span in document["spans"] if span["semantic_role"] == "product"]
        field_spans = [
            span
            for span in document["spans"]
            if span["semantic_role"] in {"dose", "frequency", "duration", "instruction", "schedule"}
        ]
        self.assertEqual({span["association_group"] for span in product_spans}, medication_groups)
        self.assertTrue(field_spans)
        self.assertTrue(all(span["association_group"] in medication_groups for span in field_spans))

    def test_counterfactual_context_reuses_exact_medication_tokens_as_non_medication_text(self) -> None:
        document = generate_parser_world(
            seed=77,
            document_index=4,
            profile=ParserWorldProfile(
                medication_count=(1, 1),
                distractor_section_count=(0, 0),
                counterfactual_context_rate=1.0,
            ),
        )
        medication_text = {
            span["text"] for span in document["spans"] if span["association_group"] is not None
        }
        context_text = {
            span["text"]
            for span in document["spans"]
            if span["semantic_role"] == "context" and span["association_group"] is None
        }
        self.assertTrue(medication_text)
        self.assertTrue(medication_text <= context_text)

    def test_geometry_scramble_changes_coordinates_without_changing_semantics_or_text(self) -> None:
        base = generate_parser_world(
            seed=88,
            document_index=2,
            profile=ParserWorldProfile(
                medication_count=(2, 2),
                distractor_section_count=(2, 2),
                counterfactual_context_rate=0.0,
                geometry_scramble_rate=0.0,
            ),
        )
        stressed = generate_parser_world(
            seed=88,
            document_index=2,
            profile=ParserWorldProfile(
                medication_count=(2, 2),
                distractor_section_count=(2, 2),
                counterfactual_context_rate=0.0,
                geometry_scramble_rate=1.0,
            ),
        )
        base_semantics = sorted(
            (span["span_id"], span["text"], span["semantic_role"], span["association_group"])
            for span in base["spans"]
        )
        stressed_semantics = sorted(
            (span["span_id"], span["text"], span["semantic_role"], span["association_group"])
            for span in stressed["spans"]
        )
        self.assertEqual(base_semantics, stressed_semantics)
        base_geometry = {span["span_id"]: span["polygon"] for span in base["spans"]}
        stressed_geometry = {span["span_id"]: span["polygon"] for span in stressed["spans"]}
        self.assertTrue(any(base_geometry[key] != stressed_geometry[key] for key in base_geometry))

        for medication in stressed["medications"]:
            group = medication["medication_id"]
            grouped = [span for span in stressed["spans"] if span["association_group"] == group]
            y_centers = [
                (float(span["polygon"][0][1]) + float(span["polygon"][2][1])) / 2.0
                for span in grouped
            ]
            self.assertLess(max(y_centers) - min(y_centers), 8.0)

        base_product = next(span for span in base["spans"] if span["semantic_role"] == "product")
        stressed_product = next(span for span in stressed["spans"] if span["span_id"] == base_product["span_id"])
        self.assertNotEqual(base_product["polygon"][0][0], stressed_product["polygon"][0][0])


class ParserV5ObservationTest(unittest.TestCase):
    def _document(self) -> dict:
        return generate_parser_world(
            seed=17,
            document_index=2,
            profile=ParserWorldProfile(
                medication_count=(1, 1),
                distractor_section_count=(0, 0),
            ),
        )

    def test_observation_corruption_is_separate_and_does_not_mutate_truth(self) -> None:
        document = self._document()
        before = copy.deepcopy(document)
        profile = ObservationProfile(
            text_corruption_rate=1.0,
            drop_rate=0.2,
            duplicate_rate=0.2,
            split_rate=0.3,
            merge_rate=0.3,
            geometry_jitter=0.02,
            false_positive_count=(2, 2),
            reading_order_shuffle_rate=1.0,
        )

        first = simulate_observations(document, seed=91, profile=profile)
        second = simulate_observations(document, seed=91, profile=profile)

        self.assertEqual(document, before)
        self.assertEqual(first, second)
        self.assertEqual(first["document_id"], document["document_id"])
        self.assertEqual(first["profile_revision"], 2)
        self.assertTrue(any(not node["source_span_ids"] for node in first["nodes"]))
        self.assertTrue(all("source_segments" in node for node in first["nodes"]))
        validate_parser_v5_document(document)

    def test_forced_merge_keeps_multiple_semantic_targets_in_one_observation(self) -> None:
        document = self._document()
        medication_id = document["medications"][0]["medication_id"]
        source_spans = [
            span
            for span in document["spans"]
            if span["association_group"] == medication_id
            and span["semantic_role"] in {"dose", "frequency"}
        ]
        self.assertEqual({span["semantic_role"] for span in source_spans}, {"dose", "frequency"})

        observation = simulate_observations(
            document,
            seed=5,
            profile=ObservationProfile(
                text_corruption_rate=0.0,
                drop_rate=0.0,
                duplicate_rate=0.0,
                split_rate=0.0,
                merge_rate=1.0,
                geometry_jitter=0.0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0.0,
            ),
        )

        merged = [
            node
            for node in observation["nodes"]
            if {target["semantic_role"] for target in node["targets"]} >= {"dose", "frequency"}
        ]
        self.assertTrue(merged)
        target = merged[0]
        self.assertEqual({item["association_group"] for item in target["targets"]}, {medication_id})
        self.assertTrue(all(item["label_status"] == "labeled" for item in target["targets"]))
        self.assertGreaterEqual(len(target["source_span_ids"]), 2)
        truth_by_span = {span["span_id"]: span for span in document["spans"]}
        self.assertGreaterEqual(len(target["source_segments"]), 2)
        for segment in target["source_segments"]:
            observed = target["text"][segment["start_char"] : segment["end_char"]]
            self.assertEqual(observed, truth_by_span[segment["source_span_id"]]["text"])

    def test_split_observation_preserves_provenance_without_forcing_flat_role(self) -> None:
        document = self._document()
        observation = simulate_observations(
            document,
            seed=13,
            profile=ObservationProfile(
                text_corruption_rate=0.0,
                drop_rate=0.0,
                duplicate_rate=0.0,
                split_rate=1.0,
                merge_rate=0.0,
                geometry_jitter=0.0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0.0,
            ),
        )

        split_nodes = [node for node in observation["nodes"] if node["operation"] == "split"]
        self.assertTrue(split_nodes)
        self.assertTrue(all(node["source_span_ids"] for node in split_nodes))
        self.assertTrue(all("semantic_role" not in node for node in observation["nodes"]))
        self.assertTrue(all("association_group" not in node for node in observation["nodes"]))

    def test_text_corruption_never_emits_empty_or_whitespace_only_node_text(self) -> None:
        document = generate_parser_world(seed=4402, document_index=0, profile=ParserWorldProfile())
        observation = simulate_observations(document, seed=4402, profile=ObservationProfile())

        self.assertTrue(observation["nodes"])
        self.assertTrue(all(str(node["text"]).strip() for node in observation["nodes"]))

    def test_text_corruption_covers_spacing_and_sequence_errors(self) -> None:
        compact_segments = [{"source_span_id": "s", "start_char": 0, "end_char": 3}]
        spaced_segments = [{"source_span_id": "s", "start_char": 0, "end_char": 5}]
        compact = {
            _corrupt_text("가나정", compact_segments, rng=random.Random(seed))[0]
            for seed in range(128)
        }
        spaced = {
            _corrupt_text("1일 2회", spaced_segments, rng=random.Random(seed))[0]
            for seed in range(128)
        }

        self.assertTrue(any(" " in text for text in compact))
        self.assertTrue(any(" " not in text for text in spaced))
        self.assertTrue(any(text not in {"가나정", "가 나정", "가나 정"} for text in compact))


if __name__ == "__main__":
    unittest.main()