from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medicine_canonical.substance_reviewed_relations import (
    APPROVED_FORM_RELATION_CORPUS_PATH,
    load_approved_form_relation_corpus,
    validate_active_form_relation_corpus,
)
from medicine_canonical.substance_matching import MatchEvidence
from medicine_canonical.substance_relation_resolution import select_source_relations


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class SubstanceReviewedRelationTest(unittest.TestCase):
    def test_checked_in_corpus_contains_only_explicit_reviewed_materials(self) -> None:
        corpus = load_approved_form_relation_corpus(
            APPROVED_FORM_RELATION_CORPUS_PATH,
            _normalize,
        )

        self.assertEqual(len(corpus), 10)
        metformin = corpus["metformin hydrochloride with colloidal anhydrous silica"]
        self.assertEqual(metformin.base_name, "Metformin Hydrochloride")
        self.assertEqual(metformin.base_unii, "786Z46389E")
        self.assertEqual(metformin.relation_type, "formulation_of")
        self.assertNotIn("metformin hydrochloride with silica", corpus)
        self.assertNotIn("meropenem with sodium carbonate", corpus)

    def test_validation_requires_active_base_to_match_pinned_unii(self) -> None:
        corpus = load_approved_form_relation_corpus(
            APPROVED_FORM_RELATION_CORPUS_PATH,
            _normalize,
        )
        key = "metformin hydrochloride with colloidal anhydrous silica"
        row = corpus[key]

        validated = validate_active_form_relation_corpus(
            {key: row},
            {_normalize(row.base_name): row.base_unii},
            _normalize,
            active_observed_names={key},
        )
        self.assertEqual(validated[key].base_unii, "786Z46389E")

        with self.assertRaisesRegex(ValueError, "pinned UNII"):
            validate_active_form_relation_corpus(
                {key: row},
                {_normalize(row.base_name): "WRONGUNII1"},
                _normalize,
                active_observed_names={key},
            )

        with self.assertRaisesRegex(ValueError, "base is not uniquely resolved"):
            validate_active_form_relation_corpus(
                {key: row},
                {},
                _normalize,
                active_observed_names={key},
            )

    def test_inactive_corpus_row_does_not_require_a_local_base(self) -> None:
        corpus = load_approved_form_relation_corpus(
            APPROVED_FORM_RELATION_CORPUS_PATH,
            _normalize,
        )
        key = "metformin hydrochloride with colloidal anhydrous silica"
        self.assertEqual(
            validate_active_form_relation_corpus(
                {key: corpus[key]},
                {},
                _normalize,
                active_observed_names=set(),
            ),
            {},
        )

    def test_direct_identity_takes_priority_over_reviewed_relation(self) -> None:
        corpus = load_approved_form_relation_corpus(
            APPROVED_FORM_RELATION_CORPUS_PATH,
            _normalize,
        )
        key = "metformin hydrochloride with colloidal anhydrous silica"
        direct = MatchEvidence(
            unii="DIRECT0001",
            external_name="DIRECT MATERIAL",
            dataset_key="fda_gsrs_unii_names:all",
            match_method="normalized_name_exact",
        )
        self.assertEqual(
            select_source_relations(
                {key: corpus[key].observed_name},
                {key: {direct.unii: direct}},
                {key: "subject-id"},
                {key: corpus[key]},
                _normalize,
            ),
            {},
        )

    def test_loader_rejects_duplicate_observed_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relations.tsv"
            path.write_text(
                "observed_name\tbase_name\tbase_unii\trelation_type\tqualifier\treview_basis\treviewed_at\n"
                "Foo With Silica\tFoo\tUNII000001\tformulation_of\tcarrier_material\treviewed\t2026-08-13\n"
                "foo with silica\tFoo\tUNII000001\tformulation_of\tcarrier_material\treviewed\t2026-08-13\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate observed_name"):
                load_approved_form_relation_corpus(path, _normalize)


if __name__ == "__main__":
    unittest.main()