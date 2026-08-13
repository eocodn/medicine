from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medicine_canonical.substance_external import ExternalEvidence
from medicine_canonical.substance_matching import candidates_for_local_name
from medicine_canonical.substance_typo_corpus import (
    APPROVED_TYPO_CORPUS_PATH,
    load_approved_typo_corpus,
    validate_approved_typo_corpus,
)


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class SubstanceTypoCorpusTest(unittest.TestCase):
    def test_checked_in_corpus_contains_only_explicit_reviewed_names(self) -> None:
        corpus = load_approved_typo_corpus(APPROVED_TYPO_CORPUS_PATH, _normalize)

        self.assertEqual(
            set(corpus),
            {
                "buprenorpine",
                "clobasam",
                "nilotinib hydrochrloride monohydrate",
                "rivastigmine tartarate",
            },
        )
        self.assertEqual(corpus["buprenorpine"].target_name, "Buprenorphine")
        self.assertEqual(corpus["buprenorpine"].target_unii, "40D3SCR4GZ")

    def test_validation_requires_unique_exact_target_and_pinned_unii(self) -> None:
        corpus = load_approved_typo_corpus(APPROVED_TYPO_CORPUS_PATH, _normalize)
        external = {
            "buprenorphine": {
                "40D3SCR4GZ": ExternalEvidence({"BUPRENORPHINE"}, "openfda_unii:all")
            },
            "clobazam": {
                "2MRO291B4U": ExternalEvidence({"CLOBAZAM"}, "openfda_unii:all")
            },
            "nilotinib hydrochloride monohydrate": {
                "5JHU0N1R6K": ExternalEvidence(
                    {"NILOTINIB HYDROCHLORIDE MONOHYDRATE"}, "openfda_unii:all"
                )
            },
            "rivastigmine tartrate": {
                "9IY2357JPE": ExternalEvidence({"RIVASTIGMINE TARTRATE"}, "openfda_unii:all")
            },
        }

        validated = validate_approved_typo_corpus(corpus, external, _normalize)
        self.assertEqual(validated["clobasam"].target_unii, "2MRO291B4U")

        bad_external = dict(external)
        bad_external["clobazam"] = {
            "WRONGUNII01": ExternalEvidence({"CLOBAZAM"}, "openfda_unii:all")
        }
        with self.assertRaisesRegex(ValueError, "pinned UNII"):
            validate_approved_typo_corpus(corpus, bad_external, _normalize)

    def test_matching_uses_only_an_explicit_full_observed_name(self) -> None:
        corpus = load_approved_typo_corpus(APPROVED_TYPO_CORPUS_PATH, _normalize)
        external = {
            "clobazam": {
                "2MRO291B4U": ExternalEvidence({"CLOBAZAM"}, "openfda_unii:all")
            }
        }
        approved = {"clobasam": corpus["clobasam"]}

        exact = candidates_for_local_name(
            "Clobasam",
            external,
            _normalize,
            approved_typos=approved,
        )
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].unii, "2MRO291B4U")
        self.assertEqual(exact[0].match_method, "approved_typo_alias")
        self.assertEqual(
            candidates_for_local_name(
                "Clobasam Hydrate",
                external,
                _normalize,
                approved_typos=approved,
            ),
            [],
        )

    def test_loader_rejects_duplicate_observed_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "typos.tsv"
            path.write_text(
                "observed_name\ttarget_name\ttarget_unii\treview_basis\treviewed_at\n"
                "Clobasam\tClobazam\t2MRO291B4U\treviewed\t2026-08-13\n"
                "clobasam\tClobazam\t2MRO291B4U\treviewed again\t2026-08-13\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate observed_name"):
                load_approved_typo_corpus(path, _normalize)


if __name__ == "__main__":
    unittest.main()