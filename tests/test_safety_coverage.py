from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.core import ConfirmationRequired, MedicationApp
from tests.safety_fixture_support import make_catalog_db, make_dur_db


class SafetyCoverageV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.app = MedicationApp(self.dur_db, self.personal_db, self.catalog_db)
        self.person = self.app.create_person(
            "사용자", "1990-01-01", "female", "not_pregnant", lactation_status="not_breastfeeding"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_exact_catalog_ingredient_can_drive_duration_warning_without_edi(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-Z", "prescription_days": 35, "start_date": "2026-08-11"},
        )

        self.assertEqual(preview["coverage"]["dataset"]["status"], "verified")
        self.assertEqual(preview["coverage"]["ingredient"]["status"], "matched")
        self.assertEqual(preview["coverage"]["ingredient"]["ingredients"], ["zolpidem"])
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "exceeded")
        self.assertEqual(preview["quantitative_checks"]["duration"]["source_scope"], "ingredient")
        duration = next(
            item for item in preview["dur_checks"] if item["category"] == "duration_caution"
        )
        self.assertEqual(duration["status"], "hit")
        self.assertTrue(preview["warning_token"])

    def test_exact_ingredient_without_quantitative_rules_is_not_applicable(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {
                "product_ref": "MFDS-I", "prescription_days": 7,
                "dose_amount": 1, "dose_unit": "캡슐", "frequency_per_day": 1,
            },
        )

        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "not_applicable")
        self.assertEqual(preview["quantitative_checks"]["dose"]["result"], "not_applicable")
        checks = {item["category"]: item for item in preview["dur_checks"]}
        self.assertEqual(len(preview["dur_checks"]), 8)
        self.assertEqual(checks["duration_caution"]["status"], "unknown")
        self.assertTrue(preview["warning_token"])

    def test_complete_dur_preview_reports_all_eight_categories_in_fixed_order(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {
                "product_ref": "MFDS-M", "prescription_days": 7,
                "dose_amount": 1, "dose_unit": "정", "frequency_per_day": 1,
            },
        )

        self.assertEqual(
            [item["category"] for item in preview["dur_checks"]],
            [
                "combination_contraindication",
                "age_contraindication",
                "pregnancy_contraindication",
                "lactation_caution",
                "elderly_caution",
                "dose_caution",
                "duration_caution",
                "therapeutic_duplication_caution",
            ],
        )
        checks = {item["category"]: item for item in preview["dur_checks"]}
        self.assertEqual(checks["pregnancy_contraindication"]["status"], "not_applicable")
        self.assertEqual(checks["lactation_caution"]["status"], "not_applicable")
        self.assertEqual(checks["elderly_caution"]["status"], "not_applicable")
        self.assertEqual(checks["duration_caution"]["status"], "clear")
        self.assertEqual(checks["dose_caution"]["status"], "not_applicable")
        self.assertFalse(any(item["status"] in {"hit", "unknown"} for item in preview["dur_checks"]))
        self.assertIsNone(preview["warning_token"])

    def test_quantitative_rule_presence_is_not_a_qualitative_finding(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-M", "prescription_days": 7},
        )

        self.assertNotIn("duration_caution", {risk["type"] for risk in preview["risks"]})
        duration = next(
            item for item in preview["dur_checks"] if item["category"] == "duration_caution"
        )
        self.assertEqual(duration["status"], "clear")

    def test_child_requires_review_even_without_a_quantitative_dur_rule(self) -> None:
        child = self.app.create_person("소아", "2015-01-01", "female", "not_pregnant")
        draft = {
            "product_ref": "MFDS-I", "prescription_days": 7,
            "dose_amount": 1, "dose_unit": "캡슐", "frequency_per_day": 1,
        }

        preview = self.app.preview_medication(child["id"], draft)

        self.assertTrue(preview["warning_token"])
        self.assertEqual(preview["quantitative_checks"]["dose"]["result"], "not_evaluable")
        self.assertIn("pediatric", preview["quantitative_checks"]["dose"]["reason"])
        with self.assertRaises(ConfirmationRequired) as blocked:
            self.app.add_medication(child["id"], **draft, request_id="child-review-required")
        self.assertEqual(self.app.list_medications(child["id"]), [])
        medication = self.app.add_medication(
            child["id"], **draft, request_id="child-review-required",
            acknowledge_warnings=True,
            warning_token=blocked.exception.assessment["warning_token"],
        )
        self.assertTrue(medication["assessment"]["acknowledged"])

    def test_unmapped_ingredient_keeps_quantitative_coverage_explicit(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-X", "prescription_days": 7, "dose_amount": 1, "dose_unit": "정"},
        )

        for dimension in ("duration", "dose"):
            check = preview["quantitative_checks"][dimension]
            self.assertEqual(check["result"], "not_evaluable")
            self.assertTrue(check["coverage_only"])

    def test_unverified_dataset_never_hides_missing_rules_as_not_applicable(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute("DELETE FROM source_files WHERE dataset_key='product:duration_caution'")
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"], {"product_ref": "MFDS-I", "prescription_days": 7}
        )

        duration = preview["quantitative_checks"]["duration"]
        self.assertEqual(duration["result"], "not_evaluable")
        self.assertTrue(duration["coverage_only"])

    def test_profile_gap_is_reported_only_for_a_matching_rule(self) -> None:
        unknown = self.app.create_person("미입력", "1990-01-01", "female", "unknown")

        unrelated = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-I"})
        related = self.app.preview_medication(unknown["id"], {"product_ref": "MFDS-Z"})
        unrelated_categories = {item["category"] for item in unrelated["coverage"]["not_evaluable_checks"]}
        related_categories = {item["category"] for item in related["coverage"]["not_evaluable_checks"]}

        self.assertNotIn("pregnancy_contraindication", unrelated_categories)
        self.assertNotIn("lactation_caution", unrelated_categories)
        # The unrelated product still has an independent product-mapping gap,
        # so the new all-unknowns-review policy may issue a token; the profile
        # categories themselves must remain absent.
        self.assertTrue(unrelated["warning_token"])
        self.assertNotIn("pregnancy_contraindication", related_categories)
        self.assertIn("lactation_caution", related_categories)
        self.assertTrue(related["warning_token"])

    def test_conditional_ingredient_duration_is_not_automatically_compared(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            """INSERT INTO ingredient_dur(
                dataset_key,source_row,category,ingredient_name,rule_value,dosage_form,note,sequence_text
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "ingredient:duration_caution", 90, "duration_caution", "Itraconazole",
                "14일", "캡슐제", "특정 적응증에 한함", "90",
            ),
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-I", "prescription_days": 7, "start_date": "2026-08-11"},
        )

        duration = preview["quantitative_checks"]["duration"]
        self.assertEqual(duration["result"], "not_evaluable")
        self.assertIn("condition", duration["reason"])

    def test_unique_normalized_name_and_ingredient_can_recover_missing_edi_product_link(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.execute(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            ("P-IBU", "건일이부프로펜연질캡슐400밀리그램_(0.4g/1캡슐)", "ING-IBU", "Ibuprofen"),
        )
        dur.execute(
            """INSERT INTO product_dur(
                dataset_key,source_row,category,ingredient_name,ingredient_code,product_name,product_code,rule_value
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "product:dose_caution", 99, "dose_caution", "Ibuprofen", "ING-IBU",
                "건일이부프로펜연질캡슐400밀리그램_(0.4g/1캡슐)", "P-IBU", "이부프로펜 3,200mg",
            ),
        )
        dur.execute(
            """INSERT INTO ingredient_dur(
                dataset_key,source_row,category,ingredient_name,rule_value,dosage_form,details,sequence_text
            ) VALUES(?,?,?,?,?,?,?,?)""",
            ("ingredient:dose_caution", 99, "dose_caution", "Ibuprofen", "이부프로펜 3,200mg", "캡슐제", None, "99"),
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "MFDS-IBU", "건일이부프로펜연질캡슐400밀리그램", "건일제약", "Ibuprofen",
                "캡슐제", None, "2023-01-01", None, "정상", "active", "fixture", "{}",
            ),
        )
        catalog.commit()
        catalog.close()

        product = self.app.get_product("MFDS-IBU")

        self.assertEqual(product["product_code"], "P-IBU")
        self.assertTrue(product["dur_match"])
        self.assertEqual(product["product_mapping_method"], "normalized_name_ingredient_unique")
        self.assertEqual(product["ingredient_mapping_status"], "matched")
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-IBU"})
        self.assertEqual([risk["type"] for risk in preview["risks"]].count("dose_caution"), 0)
        dose = next(item for item in preview["dur_checks"] if item["category"] == "dose_caution")
        self.assertEqual(dose["status"], "unknown")

    def test_name_fallback_refuses_ambiguous_dur_product_candidates(self) -> None:
        dur = sqlite3.connect(self.dur_db)
        dur.executemany(
            "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
            [
                ("P-DUP-1", "동일제품_(1정/1정)", "ING-D1", "Zolpidem"),
                ("P-DUP-2", "동일제품_(2정/1정)", "ING-D2", "Zolpidem"),
            ],
        )
        dur.commit()
        dur.close()
        catalog = sqlite3.connect(self.catalog_db)
        catalog.execute(
            """INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                permit_date,cancel_date,cancel_name,permit_status,source,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("MFDS-DUP", "동일제품", "제약", "Zolpidem", "정제", None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
        )
        catalog.commit()
        catalog.close()

        product = self.app.get_product("MFDS-DUP")

        self.assertIsNone(product["product_code"])
        self.assertFalse(product["dur_match"])

    def test_lactation_caution_applies_only_when_profile_is_breastfeeding(self) -> None:
        breastfeeding = self.app.create_person(
            "수유", "1990-01-01", "female", "not_pregnant", lactation_status="breastfeeding"
        )
        not_breastfeeding = self.app.create_person(
            "비수유", "1990-01-01", "female", "not_pregnant", lactation_status="not_breastfeeding"
        )

        active = self.app.preview_medication(breastfeeding["id"], {"product_ref": "MFDS-Z"})
        inactive = self.app.preview_medication(not_breastfeeding["id"], {"product_ref": "MFDS-Z"})

        self.assertIn("lactation_caution", {risk["type"] for risk in active["risks"]})
        self.assertNotIn("lactation_caution", {risk["type"] for risk in inactive["risks"]})

    def test_male_profile_has_no_pregnancy_or_lactation_coverage_gap(self) -> None:
        male = self.app.create_person("남성", "1990-01-01", "male")
        preview = self.app.preview_medication(male["id"], {"product_ref": "MFDS-Z"})
        categories = {item["category"] for item in preview["coverage"]["not_evaluable_checks"]}

        self.assertNotIn("pregnancy_contraindication", categories)
        self.assertNotIn("lactation_caution", categories)

    def test_warning_acknowledgement_is_bound_to_dataset_identity_and_persisted(self) -> None:
        draft = dict(product_ref="MFDS-Z", prescription_days=35, start_date="2026-08-11", request_id="z-1")
        with self.assertRaises(ConfirmationRequired) as blocked:
            self.app.add_medication(self.person["id"], **draft)
        first = blocked.exception.assessment
        old_token = first["warning_token"]
        first_dataset_id = first["dataset"]["dataset_id"]

        con = sqlite3.connect(self.dur_db)
        con.execute(
            "UPDATE source_files SET sha256=? WHERE dataset_key='ingredient:duration_caution'",
            ("f" * 64,),
        )
        con.commit()
        con.close()

        with self.assertRaises(ConfirmationRequired) as changed:
            self.app.add_medication(
                self.person["id"], **draft, acknowledge_warnings=True, warning_token=old_token
            )
        self.assertNotEqual(changed.exception.assessment["warning_token"], old_token)
        self.assertNotEqual(changed.exception.assessment["dataset"]["dataset_id"], first_dataset_id)

        assessment = changed.exception.assessment
        medication = self.app.add_medication(
            self.person["id"], **draft,
            acknowledge_warnings=True, warning_token=assessment["warning_token"],
        )
        history = self.app.list_medication_revisions(medication["id"])
        self.assertEqual(history[-1]["assessment"]["dataset"]["dataset_id"], assessment["dataset"]["dataset_id"])

    def test_warning_acknowledgement_is_invalidated_when_reviewed_safety_findings_change(self) -> None:
        draft = {
            "product_ref": "MFDS-Z",
            "prescription_days": 35,
            "start_date": "2026-08-11",
            "request_id": "profile-change-token",
        }
        preview = self.app.preview_medication(self.person["id"], draft)
        old_token = preview["warning_token"]
        self.assertTrue(old_token)
        self.assertNotIn("lactation_caution", {risk["type"] for risk in preview["risks"]})

        self.app.update_person(
            self.person["id"], self.person["name"], self.person["birth_date"], self.person["sex"],
            self.person["pregnancy_status"], "breastfeeding", self.person.get("notes"),
        )

        with self.assertRaises(ConfirmationRequired) as changed:
            self.app.add_medication(
                self.person["id"], **draft,
                acknowledge_warnings=True, warning_token=old_token,
            )
        assessment = changed.exception.assessment
        self.assertNotEqual(assessment["warning_token"], old_token)
        self.assertIn("lactation_caution", {risk["type"] for risk in assessment["risks"]})

    def test_ingredient_level_combination_contraindication_requires_review_but_can_be_registered(self) -> None:
        current_preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-I"})
        current = self.app.add_medication(
            self.person["id"], product_ref="MFDS-I", request_id="itraconazole",
            acknowledge_warnings=True, warning_token=current_preview["warning_token"],
        )
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-A"})

        combination = [risk for risk in preview["risks"] if risk["type"] == "combination_contraindication"]
        self.assertEqual(len(combination), 1)
        self.assertEqual(combination[0]["source_scope"], "ingredient")
        self.assertEqual(combination[0]["related_medication_id"], current["id"])
        self.assertNotIn("조건", combination[0]["title"])
        self.assertEqual(combination[0]["timing"]["kind"], "minimum_separation")
        self.assertEqual(combination[0]["timing"]["hours"], 24)
        combination_status = next(
            item for item in preview["dur_checks"]
            if item["category"] == "combination_contraindication"
        )
        self.assertEqual(combination_status["status"], "hit")
        conditional_checks = [
            check for check in preview["coverage"]["not_evaluable_checks"]
            if check["category"] == "combination_contraindication"
        ]
        self.assertEqual(conditional_checks, [])
        self.assertTrue(preview["warning_token"])

        with self.assertRaises(ConfirmationRequired) as blocked:
            self.app.add_medication(self.person["id"], product_ref="MFDS-A", request_id="alprazolam")
        medication = self.app.add_medication(
            self.person["id"], product_ref="MFDS-A", request_id="alprazolam",
            acknowledge_warnings=True, warning_token=blocked.exception.assessment["warning_token"],
        )
        self.assertEqual(medication["product_name"], "알프라졸람제품")

    def test_washout_structure_does_not_hide_other_unresolved_conditions(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            """UPDATE ingredient_dur
               SET note='75세 이상 남성',
                   details='Itraconazole 투여 중 및 종료 후 2주 간 해당 성분 투여 금기'
               WHERE category='combination_contraindication'"""
        )
        con.commit()
        con.close()
        current_draft = {
            "product_ref": "MFDS-I", "start_date": "2026-08-01", "prescription_days": 1,
        }
        current_preview = self.app.preview_medication(self.person["id"], current_draft)
        self.app.add_medication(
            self.person["id"], **current_draft, acknowledge_warnings=True,
            warning_token=current_preview["warning_token"],
        )

        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-A", "start_date": "2026-08-02", "prescription_days": 1},
        )
        combination = [risk for risk in preview["risks"] if risk["type"] == "combination_contraindication"]

        self.assertEqual(len(combination), 1)
        self.assertIn("조건", combination[0]["title"])
        self.assertIn("75세 이상 남성", combination[0]["details"])
        self.assertEqual(combination[0]["timing"]["kind"], "washout_after")
        self.assertEqual(combination[0]["evaluation_status"], "unknown")
        combination_status = next(
            item for item in preview["dur_checks"]
            if item["category"] == "combination_contraindication"
        )
        self.assertEqual(combination_status["status"], "unknown")

    def test_unmapped_catalog_ingredient_is_explicitly_not_evaluable(self) -> None:
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-X"})

        self.assertEqual(preview["coverage"]["ingredient"]["status"], "not_evaluable")
        self.assertTrue(preview["coverage"]["ingredient"]["reason"])
        self.assertNotIn("안전", preview["coverage"]["message"])

    def test_multiple_catalog_edi_codes_use_the_single_authoritative_dur_match(self) -> None:
        product = self.app.get_product("MFDS-M")

        self.assertEqual(product["edi_codes"], ["P-NONE", "P-LINK"])
        self.assertEqual(product["matched_product_codes"], ["P-LINK"])
        self.assertEqual(product["product_code"], "P-LINK")
        self.assertTrue(product["dur_match"])

    def test_product_link_does_not_claim_ingredient_coverage_for_unmapped_salt_name(self) -> None:
        preview = self.app.preview_medication(self.person["id"], {"product_ref": "MFDS-S"})

        self.assertEqual(preview["coverage"]["product"]["status"], "matched")
        self.assertEqual(preview["coverage"]["ingredient"]["status"], "not_evaluable")
        self.assertEqual(preview["coverage"]["ingredient"]["ingredients"], [])
        self.assertTrue(preview["warning_token"])
        checks = {item["category"]: item for item in preview["dur_checks"]}
        self.assertEqual(checks["age_contraindication"]["status"], "unknown")
        self.assertEqual(checks["pregnancy_contraindication"]["status"], "not_applicable")

    def test_unknown_dosage_form_keeps_ingredient_duration_explicitly_not_evaluable(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            {"product_ref": "MFDS-ZU", "prescription_days": 35, "start_date": "2026-08-11"},
        )

        self.assertEqual(preview["coverage"]["ingredient"]["status"], "matched")
        self.assertEqual(preview["quantitative_checks"]["duration"]["result"], "not_evaluable")
        self.assertIn("dosage form", preview["quantitative_checks"]["duration"]["reason"])
        duration_checks = [
            check for check in preview["coverage"]["not_evaluable_checks"]
            if check["category"] == "duration_caution"
        ]
        self.assertEqual(len(duration_checks), 1)
        self.assertIn("제형", duration_checks[0]["reason"])
        status = next(
            item for item in preview["dur_checks"] if item["category"] == "duration_caution"
        )
        self.assertEqual(status["status"], "unknown")
        self.assertTrue(preview["warning_token"])


if __name__ == "__main__":
    unittest.main()
