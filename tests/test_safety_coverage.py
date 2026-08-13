from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import ConfirmationRequired, MedicationApp
from tests.canonical_fixture_support import (
    add_lactation, add_linked_rule, add_product, add_unlinked_rule, create_canonical_fixture,
)


def make_canonical_db(path: Path) -> None:
    con = create_canonical_fixture(path)
    add_product(con, "MFDS-Z", "졸피뎀제품", "Zolpidem", dosage_form="정제")
    add_product(con, "MFDS-ZU", "졸피뎀제형미상제품", "Zolpidem", dosage_form=None)
    add_product(con, "MFDS-I", "이트라코나졸제품", "Itraconazole", dosage_form="캡슐제")
    add_product(con, "MFDS-A", "알프라졸람제품", "Alprazolam", dosage_form="정제")
    add_product(con, "MFDS-X", "규칙없는제품", "Mystery Salt", dosage_form="정제")
    add_product(con, "MFDS-U", "연결불완전제품", "FutureDrug Salt", dosage_form="정제")
    add_product(con, "MFDS-LU", "수유범위미확정제품", "Osimertinib Mesylate", dosage_form="정제")
    add_product(con, "MFDS-M", "정량비교졸피뎀", "Zolpidem", dosage_form="정제", edi="P-LINK")
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-Z", ingredient="Zolpidem",
        rule_value="28일", details="최대 투여기간 28일", dosage_form="정제",
    )
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-M", ingredient="Zolpidem",
        rule_value="28일", details="최대 투여기간 28일", dosage_form="정제",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-A", ingredient="Alprazolam",
        paired_item_seq="MFDS-I", paired_ingredient="Itraconazole", details="병용금기",
    )
    add_unlinked_rule(
        con, category="duration_caution", item_seq="MFDS-ZU", ingredient="Zolpidem",
        details="제형 적용범위를 확정하지 못함",
    )
    add_unlinked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-U", ingredient="FutureDrug Salt",
    )
    add_lactation(con, item_seq="MFDS-Z", ingredient="Zolpidem", details="수유 중 주의")
    add_lactation(con, item_seq="MFDS-LU", ingredient="Osimertinib", unresolved=True)
    con.commit()
    con.close()



class SafetyCoverageV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        self.app = MedicationApp(self.canonical_db, self.personal_db)
        self.person = self.app.create_person(
            "사용자", "1990-01-01", "female", "not_pregnant", lactation_status="not_breastfeeding"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
    def test_canonical_product_rule_drives_duration_without_ingredient_classifier(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-Z", "prescription_days": 35}
        )
        self.assertEqual(preview["coverage"]["dataset"]["status"], "verified")
        self.assertEqual(preview["coverage"]["ingredient"]["status"], "not_required")
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "exceeded")
        duration = next(row for row in preview["dur_checks"] if row["category"] == "duration_caution")
        self.assertEqual(duration["status"], "hit")

    def test_known_product_with_no_rule_is_clear_not_classifier_unknown(self) -> None:
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-X"})
        checks = {row["category"]: row for row in preview["dur_checks"]}
        self.assertEqual(checks["duration_caution"]["status"], "not_applicable")
        self.assertEqual(checks["combination_contraindication"]["status"], "clear")
        self.assertEqual(preview["coverage"]["product"]["identity_method"], "item_seq_exact")

    def test_unlinked_product_rule_is_explicit_unknown(self) -> None:
        pregnant = self.app.create_person(
            "임부", "1990-01-01", "female", "pregnant", lactation_status="not_breastfeeding"
        )
        preview = self.app.preview_medication(pregnant["id"], {"product_ref": "MFDS-U"})
        pregnancy = next(row for row in preview["dur_checks"] if row["category"] == "pregnancy_contraindication")
        self.assertEqual(pregnancy["status"], "unknown")
        self.assertTrue(preview["warning_token"])

    def test_unlinked_duration_rule_is_not_silently_not_applicable(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-ZU", "prescription_days": 35}
        )
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "not_evaluable")
        duration = next(row for row in preview["dur_checks"] if row["category"] == "duration_caution")
        self.assertEqual(duration["status"], "unknown")
    def test_lactation_caution_applies_only_when_breastfeeding(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        active = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-Z"})
        inactive = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-Z"})
        active_check = next(row for row in active["dur_checks"] if row["category"] == "lactation_caution")
        inactive_check = next(row for row in inactive["dur_checks"] if row["category"] == "lactation_caution")
        self.assertEqual(active_check["status"], "hit")
        self.assertEqual(inactive_check["status"], "not_applicable")

    def test_unresolved_lactation_scope_is_unknown(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        preview = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-LU"})
        check = next(row for row in preview["dur_checks"] if row["category"] == "lactation_caution")
        self.assertEqual(check["status"], "unknown")
        self.assertTrue(preview["warning_token"])

    def test_profile_gap_is_reported_only_for_relevant_canonical_rule(self) -> None:
        unknown = self.app.create_person(
            "미입력", "1990-01-01", "female", "unknown", lactation_status="unknown"
        )
        unrelated = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-I"})
        lactation = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-Z"})
        self.assertNotIn(
            "lactation_caution",
            {row["category"] for row in unrelated["coverage"]["not_evaluable_checks"]},
        )
        self.assertIn(
            "lactation_caution",
            {row["category"] for row in lactation["coverage"]["not_evaluable_checks"]},
        )

    def test_male_profile_has_no_reproductive_coverage_gap(self) -> None:
        male = self.app.create_person("남성", "1990-01-01", "male", "unknown", lactation_status="unknown")
        preview = self.app.preview_medication(male["id"], {"product_ref": "MFDS-Z"})
        categories = {row["category"] for row in preview["coverage"]["not_evaluable_checks"]}
        self.assertNotIn("pregnancy_contraindication", categories)
        self.assertNotIn("lactation_caution", categories)
    def test_warning_token_is_bound_to_canonical_dataset_identity(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        draft = {"product_ref": "MFDS-Z"}
        first = self.app.preview_medication(breastfeeding["id"], draft)
        con = sqlite3.connect(self.canonical_db)
        con.execute(
            "UPDATE source_snapshots SET sha256=? WHERE dataset_key=(SELECT dataset_key FROM source_snapshots ORDER BY dataset_key LIMIT 1)",
            ("f" * 64,),
        )
        con.commit()
        con.close()
        second = self.app.preview_medication(breastfeeding["id"], draft)
        self.assertNotEqual(first["warning_token"], second["warning_token"])

    def test_warning_token_changes_when_canonical_finding_changes(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        first = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-Z"})
        con = sqlite3.connect(self.canonical_db)
        con.execute(
            "UPDATE ingredient_rules SET details='변경된 수유 주의' WHERE category='lactation_caution' AND ingredient_name='Zolpidem'"
        )
        con.commit()
        con.close()
        second = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-Z"})
        self.assertNotEqual(first["warning_token"], second["warning_token"])

    def test_item_seq_combination_rule_requires_course_overlap(self) -> None:
        self.app.add_medication(
            self.person["id"], product_ref="MFDS-I", start_date="2026-08-01", prescription_days=10
        )
        overlapping = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-A", "start_date": "2026-08-05", "prescription_days": 2}
        )
        separated = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-A", "start_date": "2026-09-01", "prescription_days": 2}
        )
        self.assertIn("combination_contraindication", {row["type"] for row in overlapping["risks"]})
        self.assertNotIn("combination_contraindication", {row["type"] for row in separated["risks"]})

    def test_complete_preview_keeps_fixed_eight_category_order(self) -> None:
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-M", "prescription_days": 7})
        self.assertEqual(
            [row["category"] for row in preview["dur_checks"]],
            [
                "combination_contraindication", "age_contraindication", "pregnancy_contraindication",
                "lactation_caution", "elderly_caution", "dose_caution", "duration_caution",
                "therapeutic_duplication_caution",
            ],
        )


if __name__ == "__main__":
    unittest.main()
