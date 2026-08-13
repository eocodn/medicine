from __future__ import annotations

import unittest

from medicine_canonical.substance_matching import RelationEvidence, relation_for_local_name


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class SubstanceFormRelationTest(unittest.TestCase):
    def assertRelation(
        self,
        source: str,
        base: str,
        relation_type: str,
        qualifier: str,
    ) -> None:
        self.assertEqual(
            relation_for_local_name(source, _normalize),
            RelationEvidence(_normalize(base), relation_type, qualifier),
        )

    def test_transparent_remaining_form_descriptors_are_typed(self) -> None:
        self.assertRelation(
            "Perampanel(Micronized)",
            "Perampanel",
            "physical_form_of",
            "micronized",
        )
        self.assertRelation("Itraconazole Pellets", "Itraconazole", "formulation_of", "pellets")
        self.assertRelation(
            "Sodium Tetradecyl Sulfate Concentrate",
            "Sodium Tetradecyl Sulfate",
            "formulation_of",
            "concentrate",
        )
        self.assertRelation(
            "Gadoterate Meglumine Solution",
            "Gadoterate Meglumine",
            "formulation_of",
            "solution",
        )
        self.assertRelation(
            "Oxytocin Concentrate Solution",
            "Oxytocin",
            "formulation_of",
            "concentrate_solution",
        )
        self.assertRelation(
            "Dilute Nitroglycerin",
            "Nitroglycerin",
            "formulation_of",
            "dilute",
        )
        self.assertRelation(
            "Dilute Nitroglycerin Solution",
            "Nitroglycerin",
            "formulation_of",
            "dilute_solution",
        )
        self.assertRelation(
            "Dilute Nitroglycerin (Nitroglycerin 2% In Lactose)",
            "Nitroglycerin",
            "formulation_of",
            "dilute_carrier_material",
        )

    def test_chemical_and_process_annotations_remain_unclassified(self) -> None:
        for source in (
            "Atorvastatin Calcium Hydrate",
            "Filgrastim (rDNA)",
            "Metformin Hydrochloride with Colloidal Anhydrous Silica",
            "Dexibuprofen D.C.",
        ):
            self.assertIsNone(relation_for_local_name(source, _normalize), source)


if __name__ == "__main__":
    unittest.main()