from __future__ import annotations

import random
import unittest

from browser_ocr.document_parsing.parser_v5_semantic_grammar import (
    FIELD_ROLES,
    product_name_support,
    sample_distractor_texts,
    sample_medication_fields,
)


class ParserV5SemanticGrammarTest(unittest.TestCase):
    def test_product_name_spaces_are_large_and_disjoint_by_construction(self) -> None:
        train = set(product_name_support("train"))
        unseen = set(product_name_support("unseen"))
        self.assertGreaterEqual(len(train), 1000)
        self.assertGreaterEqual(len(unseen), 200)
        self.assertTrue(train.isdisjoint(unseen))

    def test_medication_surface_generation_is_compositional_and_partition_disjoint(self) -> None:
        observed: dict[str, dict[str, set[str]]] = {
            partition: {role: set() for role in FIELD_ROLES}
            for partition in ("train", "unseen")
        }
        for partition in observed:
            rng = random.Random(f"parser-v5-grammar:{partition}")
            for _ in range(256):
                fields = sample_medication_fields(rng, partition)
                self.assertEqual(set(fields), set(FIELD_ROLES))
                for role, text in fields.items():
                    self.assertTrue(text.strip())
                    observed[partition][role].add(text)
        for role in FIELD_ROLES:
            self.assertGreaterEqual(len(observed["train"][role]), 8)
            self.assertGreaterEqual(len(observed["unseen"][role]), 6)
            self.assertTrue(observed["train"][role].isdisjoint(observed["unseen"][role]))

    def test_distractor_grammar_varies_values_instead_of_replaying_fixed_records(self) -> None:
        rng = random.Random(624)
        patient_samples = {
            tuple(sample_distractor_texts(rng, "patient", "train"))
            for _ in range(20)
        }
        receipt_samples = {
            tuple(sample_distractor_texts(rng, "receipt", "train"))
            for _ in range(20)
        }
        self.assertGreater(len(patient_samples), 10)
        self.assertGreater(len(receipt_samples), 10)


if __name__ == "__main__":
    unittest.main()