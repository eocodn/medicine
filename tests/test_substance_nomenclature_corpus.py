from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medicine_canonical.substance_external import ExternalEvidence
from medicine_canonical.substance_matching import candidates_for_local_name
from medicine_canonical.substance_nomenclature_corpus import (
    APPROVED_NOMENCLATURE_CORPUS_PATH,
    load_approved_nomenclature_corpus,
    validate_approved_nomenclature_corpus,
)


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class SubstanceNomenclatureCorpusTest(unittest.TestCase):
    def test_checked_in_corpus_contains_only_reviewed_full_source_names(self) -> None:
        corpus = load_approved_nomenclature_corpus(
            APPROVED_NOMENCLATURE_CORPUS_PATH,
            _normalize,
        )

        self.assertEqual(len(corpus), 59)
        self.assertEqual(corpus["atorvastatin calcium hydrate"].target_unii, "48A5M73Z4Q")
        self.assertEqual(
            corpus["atorvastatin calcium hydrate"].external_evidence_name,
            "ATORVASTATIN CALCIUM HYDRATE [JAN]",
        )
        self.assertEqual(corpus["azithromycin hydrate"].target_unii, "5FD1131I7S")
        self.assertEqual(
            corpus["azithromycin hydrate"].external_evidence_name,
            "AZITHROMYCIN HYDRATE [JAN]",
        )
        self.assertEqual(corpus["aminophylline hydrate"].target_unii, "Y7E0LU9ZMS")
        self.assertEqual(corpus["thallium (201tl) chloride"].target_unii, "3I8Y076A0E")
        self.assertEqual(corpus["doxycycline hydrate"].target_unii, "N12000U13O")
        self.assertEqual(
            corpus["doxycycline hydrate"].external_evidence_name,
            "DOXYCYCLINE MONOHYDRATE",
        )
        self.assertEqual(corpus["sitagliptin hydrochloride hydrate"].target_unii, "6DH2XG35TG")
        self.assertEqual(corpus["norepinephrine tartrate hydrate"].target_unii, "IFY5PE3ZRW")
        self.assertEqual(
            corpus["diclofenac beta-dimethylaminoethanol"].target_unii,
            "409Q1531N0",
        )
        self.assertEqual(corpus["donepezil hydrochloride hydrate"].target_unii, "7KZL5YRL6W")
        self.assertEqual(corpus["ketoprofen lysin"].target_unii, "5WD00E3D4C")
        self.assertEqual(corpus["s-amlodipine besylate dihydrate"].target_unii, "6WFN2P6FAQ")
        self.assertEqual(corpus["glutathione (reduced)"].target_unii, "GAN16C9B8O")
        self.assertEqual(corpus["precipitated calcium carbonate"].target_unii, "H0G9379FGK")
        self.assertNotIn("s-amlodipine besylate 2.5 hydrate", corpus)
        self.assertNotIn("anhydrous risedronate sodium", corpus)
        self.assertNotIn("hyaluronidase", corpus)
        self.assertNotIn("sennae fructus", corpus)
        self.assertNotIn("anhydrous atorvastatin calcium", corpus)

    def test_validation_requires_exact_gsrs_evidence_name_and_pinned_unii(self) -> None:
        corpus = load_approved_nomenclature_corpus(
            APPROVED_NOMENCLATURE_CORPUS_PATH,
            _normalize,
        )
        row = corpus["atorvastatin calcium hydrate"]
        external = {
            _normalize(row.external_evidence_name): {
                row.target_unii: ExternalEvidence(
                    {row.external_evidence_name},
                    "fda_gsrs_unii_names:all",
                )
            }
        }

        validated = validate_approved_nomenclature_corpus(
            {"atorvastatin calcium hydrate": row},
            external,
            _normalize,
            active_observed_names={"atorvastatin calcium hydrate"},
        )
        self.assertEqual(validated["atorvastatin calcium hydrate"].target_unii, "48A5M73Z4Q")

        wrong_unii = {
            _normalize(row.external_evidence_name): {
                "WRONGUNII1": ExternalEvidence(
                    {row.external_evidence_name},
                    "fda_gsrs_unii_names:all",
                )
            }
        }
        with self.assertRaisesRegex(ValueError, "pinned UNII"):
            validate_approved_nomenclature_corpus(
                {"atorvastatin calcium hydrate": row},
                wrong_unii,
                _normalize,
                active_observed_names={"atorvastatin calcium hydrate"},
            )

    def test_matching_uses_only_explicit_full_observed_name(self) -> None:
        corpus = load_approved_nomenclature_corpus(
            APPROVED_NOMENCLATURE_CORPUS_PATH,
            _normalize,
        )
        row = corpus["atorvastatin calcium hydrate"]
        external = {
            _normalize(row.external_evidence_name): {
                row.target_unii: ExternalEvidence(
                    {row.external_evidence_name},
                    "fda_gsrs_unii_names:all",
                )
            }
        }
        approved = validate_approved_nomenclature_corpus(
            {"atorvastatin calcium hydrate": row},
            external,
            _normalize,
            active_observed_names={"atorvastatin calcium hydrate"},
        )

        matched = candidates_for_local_name(
            "Atorvastatin Calcium Hydrate",
            external,
            _normalize,
            approved_nomenclature_aliases=approved,
        )
        self.assertEqual(
            [(item.unii, item.match_method) for item in matched],
            [("48A5M73Z4Q", "approved_nomenclature_alias")],
        )

        self.assertEqual(
            candidates_for_local_name(
                "Atorvastatin Calcium Hydrates",
                external,
                _normalize,
                approved_nomenclature_aliases=approved,
            ),
            [],
        )

    def test_loader_rejects_duplicate_observed_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aliases.tsv"
            path.write_text(
                "observed_name\ttarget_unii\texternal_evidence_name\treview_basis\treviewed_at\n"
                "Foo Hydrate\tUNII000001\tFOO HYDRATE [JAN]\treviewed\t2026-08-13\n"
                "foo hydrate\tUNII000001\tFOO HYDRATE [JAN]\treviewed\t2026-08-13\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate observed_name"):
                load_approved_nomenclature_corpus(path, _normalize)


if __name__ == "__main__":
    unittest.main()