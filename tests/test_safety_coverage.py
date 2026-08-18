from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from medicine_app.canonical_safety import _mfds_criterion_note_requires_review
from medicine_app.core import ConfirmationRequired, MedicationApp
from tests.canonical_fixture_support import (
    add_linked_rule, add_product, add_unlinked_rule, create_canonical_fixture,
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
    add_product(con, "MFDS-AGE-T", "세티리진정", "Cetirizine", dosage_form=None)
    add_product(con, "MFDS-AGE-U", "세티리진제형미상", "Cetirizine", dosage_form=None)
    add_product(con, "MFDS-AGE-N", "비고연령금기제품", "DoxyLike", dosage_form="정제")
    add_product(con, "MFDS-P1", "임부1등급제품", "PregnancyGradeOne", dosage_form="정제")
    add_product(con, "MFDS-P2", "임부2등급제품", "PregnancyGradeTwo", dosage_form="정제")
    add_product(con, "MFDS-PC", "조건부임부금기제품", "PregnancyConditional", dosage_form="정제")
    add_product(con, "MFDS-PN", "비고조건임부금기제품", "PregnancyNoteConditional", dosage_form="정제")
    add_product(con, "MFDS-PA", "임부등급충돌제품", "PregnancyAmbiguous", dosage_form="정제")
    add_product(con, "MFDS-CW", "중단조건약", "ConditionalWashout", dosage_form="정제")
    add_product(con, "MFDS-CT", "중단조건대상약", "ConditionalTarget", dosage_form="정제")
    add_product(con, "MFDS-CN-A", "용량조건병용약", "ConditionalDoseA", dosage_form="정제")
    add_product(con, "MFDS-CN-B", "용량조건대상약", "ConditionalDoseB", dosage_form="정제")
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
    add_linked_rule(
        con,
        category="age_contraindication",
        item_seq="MFDS-AGE-T",
        ingredient="Cetirizine",
        rule_value="액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        product_dosage_form="필름코팅정",
        criterion_dosage_form="액제, 정제, 캡슐제",
    )
    add_linked_rule(
        con,
        category="age_contraindication",
        item_seq="MFDS-AGE-U",
        ingredient="Cetirizine",
        rule_value="액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        product_dosage_form=None,
        criterion_dosage_form="액제, 정제, 캡슐제",
    )
    add_linked_rule(
        con, category="age_contraindication", item_seq="MFDS-AGE-N",
        ingredient="DoxyLike", rule_value="12세 미만", criterion_qualifier_note="다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여",
        details="12세 미만 소아 주의", dosage_form="정제",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-P1",
        ingredient="PregnancyGradeOne", rule_value="1등급", details="임부금기 1등급",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-P2",
        ingredient="PregnancyGradeTwo", rule_value="2", details="임부금기 2등급",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PC",
        ingredient="PregnancyConditional", rule_value="2등급(말라리아 치료시 제외)",
        details="말라리아 치료 목적이면 예외가 될 수 있음",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PN",
        ingredient="PregnancyNoteConditional", rule_value="2등급",
        criterion_qualifier_note="단, 강심제로 사용시 제외",
        details="강심제 사용 여부에 따라 예외가 있음",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PA",
        ingredient="PregnancyAmbiguous", rule_value="1등급", details="적응증 A",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PA",
        ingredient="PregnancyAmbiguous", rule_value="2등급", details="적응증 B",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-CW",
        ingredient="ConditionalWashout", paired_item_seq="MFDS-CT",
        paired_ingredient="ConditionalTarget",
        details="ConditionalWashout 중단한 직후에는 ConditionalTarget 시작할 수 없음",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-CN-A",
        ingredient="ConditionalDoseA", paired_item_seq="MFDS-CN-B",
        paired_ingredient="ConditionalDoseB", details="혈액학적 독성 증가",
        criterion_qualifier_note="ConditionalDoseB 1주에 20mg 이상 투여시",
    )
    add_unlinked_rule(
        con, category="duration_caution", item_seq="MFDS-ZU", ingredient="Zolpidem",
        details="제형 적용범위를 확정하지 못함",
    )
    add_unlinked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-U", ingredient="FutureDrug Salt",
    )
    # The mobile release gate verifies bridge materialization, so this synthetic
    # canonical fixture carries representative bridge rows instead of bypassing
    # release verification in tests.
    criterion_id = con.execute("SELECT id FROM ingredient_rules ORDER BY id LIMIT 1").fetchone()[0]
    con.execute(
        "INSERT INTO dur_ingredient_concepts(concept_id,category,ingredient_code) VALUES('fixture:concept','duration_caution','D-MFDS-Z')"
    )
    con.execute(
        """INSERT INTO dur_product_item_signatures(
               item_seq,signature_type,signature_key,component_count,match_method,evidence_kind
           ) VALUES('MFDS-Z','code','D-MFDS-Z',1,'mfds_ingredient_code','fixture')"""
    )
    con.execute(
        """INSERT INTO dur_criterion_signatures(
               criterion_rule_id,category,effect_key,signature_key,match_method,evidence_kind
           ) VALUES(?,'duration_caution','','D-MFDS-Z','mfds_ingredient_code','fixture')""",
        (criterion_id,),
    )
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

    def test_mfds_ingredient_remark_requires_review_without_becoming_executable_logic(self) -> None:
        self.assertTrue(_mfds_criterion_note_requires_review({
            "criterion_source_dataset_key": "mfds_dur_ingredient:getPwnmTabooInfoList02",
            "category": "pregnancy_contraindication",
            "criterion_qualifier_note": "단, 강심제로 사용시 제외",
        }))
        self.assertTrue(_mfds_criterion_note_requires_review({
            "criterion_source_dataset_key": "mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02",
            "category": "age_contraindication",
            "ingredient_code": "D000503",
            "item_seq": "198000105",
            "criterion_rule_value": "12세 미만",
            "criterion_qualifier_note": "다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여",
        }))
        self.assertFalse(_mfds_criterion_note_requires_review({
            "criterion_source_dataset_key": "mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02",
            "category": "age_contraindication",
            "ingredient_code": "D000656",
            "item_seq": "199403253",
            "criterion_rule_value": "12세 미만",
            "criterion_qualifier_note": "점안제(1%)",
        }))

    def test_canonical_product_rule_drives_duration_without_ingredient_classifier(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-Z", "prescription_days": 35}
        )
        self.assertEqual(preview["coverage"]["dataset"]["status"], "verified")
        self.assertEqual(preview["coverage"]["ingredient"]["status"], "not_required")
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "exceeded")
        duration = next(row for row in preview["dur_checks"] if row["category"] == "duration_caution")
        self.assertEqual(duration["status"], "hit")

    def test_mfds_age_remark_requires_professional_review_without_parsing_it(self) -> None:
        with sqlite3.connect(self.canonical_db) as con:
            con.execute(
                "UPDATE ingredient_rules SET source_dataset_key=? WHERE ingredient_name='DoxyLike'",
                ("mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02",),
            )
        child = self.app.create_person(
            "소아", "2016-01-01", "male", "not_applicable", lactation_status="not_applicable"
        )
        preview = self.app.preview_medication(child["id"], {"product_ref": "MFDS-AGE-N"})
        age = next(row for row in preview["dur_checks"] if row["category"] == "age_contraindication")
        self.assertEqual(age["status"], "conditional")
        self.assertIn("의사", age["details"])
        self.assertIn("약사", age["details"])
        self.assertNotIn("다만", age["summary"])

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
        self.assertIn("의사", pregnancy["details"])
        self.assertIn("약사", pregnancy["details"])
        self.assertTrue(preview["warning_token"])

    def test_unconditional_pregnancy_grades_are_distinct_definitive_hits(self) -> None:
        pregnant = self.app.create_person(
            "임부등급", "1990-01-01", "female", "pregnant", lactation_status="not_breastfeeding"
        )

        grade_one = self.app.preview_medication(pregnant["id"], {"product_ref": "MFDS-P1"})
        grade_two = self.app.preview_medication(pregnant["id"], {"product_ref": "MFDS-P2"})

        one = next(row for row in grade_one["dur_checks"] if row["category"] == "pregnancy_contraindication")
        two = next(row for row in grade_two["dur_checks"] if row["category"] == "pregnancy_contraindication")
        self.assertEqual(one["status"], "hit")
        self.assertEqual(two["status"], "hit")
        self.assertIn("1등급", one["summary"])
        self.assertIn("2등급", two["summary"])
        self.assertTrue(grade_one["warning_token"])
        self.assertTrue(grade_two["warning_token"])

    def test_exception_bearing_pregnancy_rule_is_conditional_not_unknown(self) -> None:
        pregnant = self.app.create_person(
            "조건부임부", "1990-01-01", "female", "pregnant", lactation_status="not_breastfeeding"
        )

        preview = self.app.preview_medication(pregnant["id"], {"product_ref": "MFDS-PC", "long_term": True})

        pregnancy = next(row for row in preview["dur_checks"] if row["category"] == "pregnancy_contraindication")
        self.assertEqual(pregnancy["status"], "conditional")
        self.assertIn("2등급", pregnancy["summary"])
        self.assertIn("말라리아 치료시 제외", pregnancy["summary"])
        self.assertEqual(pregnancy["findings"][0]["evaluation_status"], "conditional")
        self.assertTrue(preview["warning_token"])

        medication = self.app.add_medication(
            pregnant["id"], product_ref="MFDS-PC", long_term=True, acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )
        current = next(
            row for row in self.app.list_medications(pregnant["id"])
            if row["id"] == medication["id"]
        )
        current_pregnancy = next(
            row for row in current["current_assessment"]["dur_checks"]
            if row["category"] == "pregnancy_contraindication"
        )
        self.assertTrue(current["dur_alert"])
        self.assertEqual(current_pregnancy["status"], "conditional")

    def test_pregnancy_exception_in_mfds_qualifier_remains_conditional(self) -> None:
        pregnant = self.app.create_person(
            "비고조건임부", "1990-01-01", "female", "pregnant", lactation_status="not_breastfeeding"
        )

        preview = self.app.preview_medication(
            pregnant["id"], {"product_ref": "MFDS-PN", "long_term": True}
        )

        pregnancy = next(
            row for row in preview["dur_checks"]
            if row["category"] == "pregnancy_contraindication"
        )
        self.assertEqual(pregnancy["status"], "conditional")
        self.assertIn("2등급", pregnancy["summary"])
        self.assertNotIn("강심제로 사용시 제외", pregnancy["summary"])
        self.assertIn("의사", pregnancy["details"])
        self.assertIn("약사", pregnancy["details"])
        self.assertEqual(pregnancy["findings"][0]["evaluation_status"], "conditional")

    def test_conflicting_pregnancy_grades_require_review_instead_of_definitive_hit(self) -> None:
        pregnant = self.app.create_person(
            "등급충돌임부", "1990-01-01", "female", "pregnant", lactation_status="not_breastfeeding"
        )
        preview = self.app.preview_medication(pregnant["id"], {"product_ref": "MFDS-PA"})
        pregnancy = next(
            row for row in preview["dur_checks"]
            if row["category"] == "pregnancy_contraindication"
        )
        self.assertEqual(pregnancy["status"], "unknown")
        self.assertIn("의사", pregnancy["details"])
        self.assertIn("약사", pregnancy["details"])
        self.assertTrue(preview["warning_token"])

    def test_known_interaction_with_unresolved_timing_is_conditional(self) -> None:
        person = self.app.create_person(
            "병용조건", "1990-01-01", "male", "not_applicable", lactation_status="not_applicable"
        )
        self.app.add_medication(
            person["id"], product_ref="MFDS-CW", start_date="2026-08-01", prescription_days=3,
        )

        preview = self.app.preview_medication(
            person["id"],
            {"product_ref": "MFDS-CT", "start_date": "2026-08-10", "prescription_days": 3},
        )

        interaction = next(row for row in preview["dur_checks"] if row["category"] == "combination_contraindication")
        self.assertEqual(interaction["status"], "conditional")
        self.assertEqual(interaction["findings"][0]["evaluation_status"], "conditional")
        self.assertEqual(interaction["findings"][0]["timing"]["status"], "not_evaluable")
        self.assertTrue(preview["warning_token"])

    def test_interaction_with_unmodeled_mfds_qualifier_is_conditional(self) -> None:
        person = self.app.create_person(
            "병용용량조건", "1990-01-01", "male", "not_applicable", lactation_status="not_applicable"
        )
        self.app.add_medication(person["id"], product_ref="MFDS-CN-B", long_term=True)

        preview = self.app.preview_medication(
            person["id"], {"product_ref": "MFDS-CN-A", "long_term": True}
        )

        interaction = next(row for row in preview["dur_checks"] if row["category"] == "combination_contraindication")
        self.assertEqual(interaction["status"], "conditional")
        self.assertEqual(interaction["findings"][0]["evaluation_status"], "conditional")
        self.assertTrue(preview["warning_token"])

    def test_multi_form_age_rule_uses_the_threshold_for_the_product_form(self) -> None:
        child = self.app.create_person(
            "4세", "2022-01-01", "female", "not_pregnant", lactation_status="not_breastfeeding"
        )

        preview = self.app.preview_medication(
            child["id"], {"product_ref": "MFDS-AGE-T"}, as_of=date(2026, 8, 13)
        )

        age = next(row for row in preview["dur_checks"] if row["category"] == "age_contraindication")
        self.assertEqual(age["status"], "hit")
        self.assertIn("6세 미만", age["summary"])

    def test_multi_form_age_rule_without_resolvable_product_form_fails_closed(self) -> None:
        child = self.app.create_person(
            "4세-제형미상", "2022-01-01", "female", "not_pregnant", lactation_status="not_breastfeeding"
        )

        preview = self.app.preview_medication(
            child["id"], {"product_ref": "MFDS-AGE-U"}, as_of=date(2026, 8, 13)
        )

        age = next(row for row in preview["dur_checks"] if row["category"] == "age_contraindication")
        self.assertEqual(age["status"], "unknown")
        self.assertTrue(preview["warning_token"])

    def test_unlinked_duration_rule_is_not_silently_not_applicable(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-ZU", "prescription_days": 35}
        )
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "not_evaluable")
        duration = next(row for row in preview["dur_checks"] if row["category"] == "duration_caution")
        self.assertEqual(duration["status"], "unknown")
    def test_lactation_caution_is_not_exposed_even_when_legacy_data_exists(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        active = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-Z"})
        inactive = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-Z"})
        self.assertNotIn("lactation_caution", {row["category"] for row in active["dur_checks"]})
        self.assertNotIn("lactation_caution", {row["category"] for row in inactive["dur_checks"]})

    def test_unresolved_lactation_scope_does_not_create_a_supported_check(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        preview = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-LU"})
        self.assertNotIn("lactation_caution", {row["category"] for row in preview["dur_checks"]})

    def test_profile_gap_is_reported_only_for_relevant_canonical_rule(self) -> None:
        # Legacy profiles may still contain unanswered reproductive fields. New
        # writes reject these states, but runtime evaluation must remain fail-closed
        # until the user reviews the migrated profile.
        con = sqlite3.connect(self.personal_db)
        con.execute(
            "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status) VALUES(?,?,?,?,?,?)",
            ("legacy-unknown", "미입력", "1990-01-01", "female", "unknown", "unknown"),
        )
        con.commit()
        con.close()
        unknown = self.app.get_person("legacy-unknown")
        unrelated = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-I"})
        lactation = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-Z"})
        self.assertNotIn(
            "lactation_caution",
            {row["category"] for row in unrelated["coverage"]["not_evaluable_checks"]},
        )
        self.assertNotIn(
            "lactation_caution",
            {row["category"] for row in lactation["coverage"]["not_evaluable_checks"]},
        )

    def test_male_profile_has_no_reproductive_coverage_gap(self) -> None:
        male = self.app.create_person("남성", "1990-01-01", "male", "not_applicable", lactation_status="not_applicable")
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
        self.assertEqual(first["warning_token"], second["warning_token"])

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

    def test_complete_preview_keeps_fixed_supported_category_order(self) -> None:
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-M", "prescription_days": 7})
        self.assertEqual(
            [row["category"] for row in preview["dur_checks"]],
            [
                "combination_contraindication", "age_contraindication", "pregnancy_contraindication",
                "elderly_caution", "dose_caution", "duration_caution", "therapeutic_duplication_caution",
            ],
        )


if __name__ == "__main__":
    unittest.main()
