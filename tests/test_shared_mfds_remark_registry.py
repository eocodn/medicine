from __future__ import annotations

from pathlib import Path
import unittest

from medicine_reference.mfds_remark_registry import (
    ReviewedMfdsRemark,
    reviewed_mfds_remark,
    reviewed_mfds_remark_count,
    reviewed_mfds_remark_counts_by_category,
)


class SharedMfdsRemarkRegistryTest(unittest.TestCase):
    def test_shared_registry_preserves_exact_reviewed_contract(self) -> None:
        self.assertEqual(reviewed_mfds_remark_count(), 110)
        self.assertEqual(
            reviewed_mfds_remark_counts_by_category(),
            {
                "age_contraindication": 12,
                "combination_contraindication": 24,
                "dose_caution": 16,
                "duration_caution": 7,
                "elderly_caution": 0,
                "pregnancy_contraindication": 49,
                "therapeutic_duplication_caution": 2,
            },
        )
        qualifier = reviewed_mfds_remark("dose_caution", "단일제·복합제 포함")
        self.assertIsInstance(qualifier, ReviewedMfdsRemark)
        self.assertEqual(qualifier.mode, "composition_scope")
        self.assertEqual(qualifier.value, "all")
        no_space_24h = reviewed_mfds_remark(
            "combination_contraindication", "24시간이내 병용금기"
        )
        self.assertEqual(no_space_24h.mode, "interaction_window")
        self.assertEqual(no_space_24h.value, "24")
        for remark, hours in (
            ("병용 시 최소 한 시간 이상 간격을 두고 투여함", "1"),
            ("36시간 이내 병용금기", "36"),
            ("1주이내 병용금기", "168"),
        ):
            timing = reviewed_mfds_remark("combination_contraindication", remark)
            self.assertEqual(timing.mode, "interaction_window")
            self.assertEqual(timing.value, hours)
        self.assertTrue(
            reviewed_mfds_remark(
                "combination_contraindication", "75세 이상 남성"
            ).requires_review
        )
        self.assertTrue(
            reviewed_mfds_remark(
                "pregnancy_contraindication", "단, VitA(레티놀)로서 5000 I.U/1일 이상"
            ).requires_review
        )
        exact_multiline = (
            '"(정제) \n'
            '임신 초기 4개월동안 투여시 기형아 출산 가능성(태아의 수족기형현상 4.7배 높게 보고됨).\n'
            '(현탁액)\n'
            '임부 투여시 태아에 치명적인 위해 가능성.\n'
            '동물실험에서 수컷 새끼의 번식능력 손상, 태자의 체중감소, 생존출생 태자수의 감소, 수컷 태자의 여성화 등 보고."'
        )
        self.assertEqual(
            reviewed_mfds_remark("pregnancy_contraindication", exact_multiline).mode,
            "informational",
        )
        with self.assertRaisesRegex(ValueError, "unreviewed MFDS REMARK"):
            reviewed_mfds_remark("dose_caution", "새로운 미검토 비고")

    def test_app_runtime_does_not_import_review_registry_at_module_load(self) -> None:
        source = Path("medicine_app/canonical_safety.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "from medicine_reference.mfds_remark_registry import ReviewedMfdsRemark",
            source,
        )
        self.assertIn("reference_criterion_semantics", source)

    def test_registry_has_one_server_review_location_and_is_not_android_packaged(self) -> None:
        self.assertFalse(Path("medicine_canonical/mfds_remark_registry.py").exists())
        self.assertFalse(Path("medicine_canonical/data/mfds_remark_registry.tsv").exists())
        self.assertTrue(Path("medicine_reference/mfds_remark_registry.py").is_file())
        self.assertTrue(Path("medicine_reference/data/mfds_remark_registry.tsv").is_file())

        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('medicine_reference = ["data/*.tsv"]', pyproject)

        gradle = Path("android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertNotIn('include("medicine_reference/**/*.py")', gradle)
        self.assertNotIn('include("medicine_reference/data/mfds_remark_registry.tsv")', gradle)
        self.assertNotIn('include("medicine_canonical/mfds_remark_registry.py")', gradle)
        self.assertNotIn('include("medicine_canonical/data/mfds_remark_registry.tsv")', gradle)


if __name__ == "__main__":
    unittest.main()