from __future__ import annotations

from pathlib import Path
import unittest

from medicine_canonical.mfds_ingredient import MFDS_INGREDIENT_ENDPOINTS
from medicine_canonical.schema import CORE_SOURCE_FAMILIES
from medicine_canonical.source_policy import (
    CANONICAL_SOURCE_POLICY,
    EXPECTED_CANONICAL_SOURCE_FAMILIES,
    EXPECTED_CANONICAL_SOURCE_KEYS,
)
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_DATASET_KEY, PERMIT_FILENAME
from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION,
    MFDS_DUR_ITEM_SOURCES_BY_OPERATION,
    MFDS_SOURCE_FAMILIES,
    MFDS_SOURCE_FAMILY_SET,
    MFDS_SOURCE_KEYS,
    MFDS_SOURCE_MANIFEST,
    MFDS_SOURCE_POLICY,
    PERMIT_SOURCE,
)


class MfdsSourceManifestTest(unittest.TestCase):
    def test_manifest_is_the_single_authoritative_17_source_inventory(self) -> None:
        self.assertEqual(len(MFDS_SOURCE_MANIFEST), 17)
        self.assertEqual(len(MFDS_DUR_ITEM_SOURCES_BY_OPERATION), 9)
        self.assertEqual(len(MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION), 7)
        self.assertEqual(PERMIT_SOURCE.dataset_key, "mfds_permit:products")
        self.assertEqual(PERMIT_SOURCE.filename, "mfds_permit_products.jsonl")

        self.assertIs(EXPECTED_CANONICAL_SOURCE_FAMILIES, MFDS_SOURCE_FAMILIES)
        self.assertIs(EXPECTED_CANONICAL_SOURCE_KEYS, MFDS_SOURCE_KEYS)
        self.assertIs(CORE_SOURCE_FAMILIES, MFDS_SOURCE_FAMILY_SET)
        self.assertEqual(CANONICAL_SOURCE_POLICY, MFDS_SOURCE_POLICY)

    def test_manifest_preserves_existing_endpoint_and_filename_contract(self) -> None:
        expected_items = {
            "getUsjntTabooInfoList03": ("combination_contraindication", "rule", "dur_combination.jsonl"),
            "getSpcifyAgrdeTabooInfoList03": ("age_contraindication", "rule", "dur_age.jsonl"),
            "getPwnmTabooInfoList03": ("pregnancy_contraindication", "rule", "dur_pregnancy.jsonl"),
            "getCpctyAtentInfoList03": ("dose_caution", "rule", "dur_dose.jsonl"),
            "getMdctnPdAtentInfoList03": ("duration_caution", "rule", "dur_duration.jsonl"),
            "getOdsnAtentInfoList03": ("elderly_caution", "rule", "dur_elderly.jsonl"),
            "getEfcyDplctInfoList03": (
                "therapeutic_duplication_caution",
                "rule",
                "dur_duplication.jsonl",
            ),
            "getDurPrdlstInfoList03": ("dur_product_info", "flags", "dur_product_info.jsonl"),
            "getSeobangjeongPartitnAtentInfoList03": ("split_caution", "split", "dur_split.jsonl"),
        }
        self.assertEqual(
            {
                operation: (source.category, source.kind, source.filename)
                for operation, source in MFDS_DUR_ITEM_SOURCES_BY_OPERATION.items()
            },
            expected_items,
        )

        expected_ingredients = {
            "getUsjntTabooInfoList02": ("combination_contraindication", "dur_ingredient_combination.jsonl", None, True),
            "getSpcifyAgrdeTabooInfoList02": ("age_contraindication", "dur_ingredient_age.jsonl", "AGE_BASE", True),
            "getPwnmTabooInfoList02": ("pregnancy_contraindication", "dur_ingredient_pregnancy.jsonl", "GRADE", True),
            "getCpctyAtentInfoList02": ("dose_caution", "dur_ingredient_dose.jsonl", "MAX_QTY", False),
            "getMdctnPdAtentInfoList02": (
                "duration_caution",
                "dur_ingredient_duration.jsonl",
                "MAX_DOSAGE_TERM",
                True,
            ),
            "getOdsnAtentInfoList02": ("elderly_caution", "dur_ingredient_elderly.jsonl", None, True),
            "getEfcyDplctInfoList02": (
                "therapeutic_duplication_caution",
                "dur_ingredient_duplication.jsonl",
                "EFFECT_CODE",
                True,
            ),
        }
        self.assertEqual(
            {
                operation: (
                    source.category,
                    source.filename,
                    source.rule_field,
                    source.rule_required,
                )
                for operation, source in MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION.items()
            },
            expected_ingredients,
        )

    def test_existing_builder_endpoint_views_are_derived_from_shared_manifest(self) -> None:
        self.assertEqual(PERMIT_DATASET_KEY, PERMIT_SOURCE.dataset_key)
        self.assertEqual(PERMIT_FILENAME, PERMIT_SOURCE.filename)
        self.assertIs(DUR_ENDPOINTS, MFDS_DUR_ITEM_SOURCES_BY_OPERATION)
        self.assertIs(MFDS_INGREDIENT_ENDPOINTS, MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION)

        for operation, source in DUR_ENDPOINTS.items():
            self.assertEqual(source.dataset_key, f"mfds_dur:{operation}")
            self.assertEqual(source.source_family, "mfds_dur_item_api")
            self.assertEqual(source.source_locator.rsplit("/", 1)[-1], operation)

        for operation, source in MFDS_INGREDIENT_ENDPOINTS.items():
            self.assertEqual(source.dataset_key, f"mfds_dur_ingredient:{operation}")
            self.assertEqual(source.source_family, "mfds_dur_ingredient_api")
            self.assertEqual(source.source_locator.rsplit("/", 1)[-1], operation)

    def test_shared_manifest_is_packaged_only_for_python_builder_tools(self) -> None:
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        android = Path("android/app/build.gradle.kts").read_text(encoding="utf-8")
        dockerfile = Path("Dockerfile.dev").read_text(encoding="utf-8")

        self.assertIn('"medicine_reference*"', pyproject)
        self.assertNotIn("COPY medicine_reference", dockerfile)
        self.assertNotIn("COPY medicine_canonical", dockerfile)
        self.assertNotIn('include("medicine_reference/**/*.py")', android)
        self.assertNotIn("mfds_remark_registry.tsv", android)


if __name__ == "__main__":
    unittest.main()
