from __future__ import annotations

from pathlib import Path
import unittest

from medicine_app import canonical_safety
from medicine_reference.mfds_remark_registry import (
    ReviewedMfdsRemark,
    reviewed_mfds_remark,
    reviewed_mfds_remark_count,
    reviewed_mfds_remark_counts_by_category,
)


class SharedMfdsRemarkRegistryTest(unittest.TestCase):
    def test_shared_registry_preserves_exact_reviewed_contract(self) -> None:
        self.assertEqual(reviewed_mfds_remark_count(), 69)
        self.assertEqual(
            reviewed_mfds_remark_counts_by_category(),
            {
                "age_contraindication": 8,
                "combination_contraindication": 11,
                "dose_caution": 12,
                "duration_caution": 4,
                "elderly_caution": 0,
                "pregnancy_contraindication": 32,
                "therapeutic_duplication_caution": 2,
            },
        )
        qualifier = reviewed_mfds_remark("dose_caution", "단일제·복합제 포함")
        self.assertIsInstance(qualifier, ReviewedMfdsRemark)
        self.assertEqual(qualifier.mode, "composition_scope")
        self.assertEqual(qualifier.value, "all")
        with self.assertRaisesRegex(ValueError, "unreviewed MFDS REMARK"):
            reviewed_mfds_remark("dose_caution", "새로운 미검토 비고")

    def test_app_runtime_uses_shared_registry_without_canonical_dependency(self) -> None:
        self.assertIs(canonical_safety.ReviewedMfdsRemark, ReviewedMfdsRemark)
        source = Path("medicine_app/canonical_safety.py").read_text(encoding="utf-8")
        self.assertIn("from medicine_reference.mfds_remark_registry import", source)
        self.assertNotIn("medicine_canonical.mfds_remark_registry", source)

    def test_registry_has_one_runtime_packaged_location(self) -> None:
        self.assertFalse(Path("medicine_canonical/mfds_remark_registry.py").exists())
        self.assertFalse(Path("medicine_canonical/data/mfds_remark_registry.tsv").exists())
        self.assertTrue(Path("medicine_reference/mfds_remark_registry.py").is_file())
        self.assertTrue(Path("medicine_reference/data/mfds_remark_registry.tsv").is_file())

        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('medicine_reference = ["data/*.tsv"]', pyproject)

        gradle = Path("android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn('include("medicine_reference/data/mfds_remark_registry.tsv")', gradle)
        self.assertNotIn('include("medicine_canonical/mfds_remark_registry.py")', gradle)
        self.assertNotIn('include("medicine_canonical/data/mfds_remark_registry.tsv")', gradle)


if __name__ == "__main__":
    unittest.main()