from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_app.core import MedicationApp
from medicine_app.products import ProductRepository


def make_canonical_runtime_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        snapshots = [
            ("mfds_permit:products", "mfds_permit_api"),
            ("mfds_dur:getUsjntTabooInfoList03", "mfds_dur_item_api"),
            ("kids_mfds_xlsx:lactation_caution", "kids_mfds_xlsx"),
        ]
        for index, (key, family) in enumerate(snapshots, 1):
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?)""",
                (key, family, key, key, 1, f"{index:064x}", "{}"),
            )
        con.executemany(
            "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
            [
                ("schema_version", SCHEMA_VERSION),
                ("build_stage", "complete"),
                ("built_at", "2026-08-13T15:00:00+09:00"),
                ("source_policy", "mfds_permit_api+mfds_dur_item_api+kids_mfds_xlsx"),
                ("unresolved_link_ambiguities", "[]"),
            ],
        )
        con.executemany(
            """INSERT INTO products(
                   item_seq,source_row,product_name,manufacturer,ingredient_text,dosage_form,
                   permit_date,permit_status,source_dataset_key
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                ("P-Z", 1, "졸피뎀정", "제약", "Zolpidem", "정제", "2020-01-01", "active", "mfds_permit:products"),
                ("P-I", 2, "이트라코나졸캡슐", "제약", "Itraconazole", "캡슐제", "2020-01-02", "active", "mfds_permit:products"),
                ("P-U", 3, "오시머티닙정", "제약", "Osimertinib Mesylate", "정제", "2020-01-03", "active", "mfds_permit:products"),
            ],
        )
        # Product-level linked criteria: duration and combination.
        con.executemany(
            """INSERT INTO product_rules(
                   id,source_dataset_key,source_row,category,item_seq,ingredient_code,
                   ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
                   paired_ingredient_name,paired_ingredient_name_en,details
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "mfds_dur:getUsjntTabooInfoList03", 1, "duration_caution", "P-Z", "D-Z", "졸피뎀", "Zolpidem", None, None, None, None, "최대 28일"),
                (2, "mfds_dur:getUsjntTabooInfoList03", 2, "combination_contraindication", "P-Z", "D-Z", "졸피뎀", "Zolpidem", "P-I", "D-I", "이트라코나졸", "Itraconazole", "병용금기"),
            ],
        )
        con.executemany(
            """INSERT INTO ingredient_rules(
                   id,source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
                   paired_ingredient_name,rule_value,dosage_form,note,details,sequence_text
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "kids_mfds_xlsx:lactation_caution", 11, "duration_caution", "Zolpidem", "졸피뎀", None, "28일", "정제", None, "최대 28일", "1"),
                (2, "kids_mfds_xlsx:lactation_caution", 12, "combination_contraindication", "Zolpidem", "졸피뎀", "Itraconazole", None, None, None, "병용금기", "2"),
                (3, "kids_mfds_xlsx:lactation_caution", 13, "lactation_caution", "Zolpidem", "졸피뎀", None, None, None, None, "수유부주의", "3"),
                (4, "kids_mfds_xlsx:lactation_caution", 14, "lactation_caution", "Osimertinib", "오시머티닙", None, None, None, None, "수유부주의", "4"),
            ],
        )
        con.executemany(
            "INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id,match_method,pair_orientation) VALUES(?,?,?,?)",
            [(1, 1, "english_exact", None), (2, 2, "english_exact", "forward")],
        )
        con.execute(
            """INSERT INTO product_ingredient_criterion_links(
                   item_seq,criterion_rule_id,category,match_method,evidence_kind,evidence_json
               ) VALUES('P-Z',3,'lactation_caution','precise_substance_exact','precise_substance_identity','{}')"""
        )
        con.execute(
            """INSERT INTO product_ingredient_criterion_unresolved(
                   item_seq,criterion_rule_id,category,reason,evidence_json
               ) VALUES('P-U',4,'lactation_caution','scope_relation_unproven','{}')"""
        )
        con.execute(
            """INSERT INTO product_flags(
                   source_dataset_key,source_row,flag_ordinal,item_seq,category,flag_code,flag_name,details
               ) VALUES('mfds_dur:getUsjntTabooInfoList03',50,0,'P-Z','split_caution','S','분할주의','분할불가')"""
        )
        con.commit()
    finally:
        con.close()


class CanonicalRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical.sqlite"
        self.personal = self.root / "personal.sqlite"
        make_canonical_runtime_db(self.canonical)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_product_repository_uses_item_seq_without_legacy_classifier_tables(self) -> None:
        repo = ProductRepository(self.canonical)
        product = repo.get("P-Z")
        self.assertEqual(product["catalog_item_seq"], "P-Z")
        self.assertEqual(product["product_mapping_status"], "matched")
        self.assertEqual(product["product_mapping_method"], "item_seq_exact")
        self.assertEqual(product["product_code"], "P-Z")
        self.assertEqual(product["ingredient_name"], "Zolpidem")
        self.assertEqual(product["product_flags"][0]["category"], "split_caution")
        with closing(sqlite3.connect(self.canonical)) as con:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("product_catalog", names)
        self.assertNotIn("product_code_bridge", names)
        self.assertNotIn("ingredient_aliases", names)

    def test_app_uses_canonical_product_and_lactation_criteria(self) -> None:
        app = MedicationApp(self.canonical, self.personal)
        person = app.create_person(
            "수유자", "1990-01-01", sex="female",
            pregnancy_status="not_pregnant", lactation_status="breastfeeding",
        )
        preview = app.preview_medication(person["id"], {
            "product_ref": "P-Z",
            "prescription_days": 30,
        })
        by_category = {row["category"]: row for row in preview["dur_checks"]}
        self.assertEqual(by_category["lactation_caution"]["status"], "hit")
        self.assertEqual(by_category["duration_caution"]["status"], "hit")
        self.assertEqual(preview["coverage"]["product"]["identity_method"], "item_seq_exact")
        self.assertEqual(preview["coverage"]["dataset"]["status"], "verified")

    def test_unresolved_lactation_scope_is_explicit_unknown(self) -> None:
        app = MedicationApp(self.canonical, self.personal)
        person = app.create_person(
            "수유자", "1990-01-01", sex="female",
            pregnancy_status="not_pregnant", lactation_status="breastfeeding",
        )
        preview = app.preview_medication(person["id"], {"product_ref": "P-U"})
        lactation = next(row for row in preview["dur_checks"] if row["category"] == "lactation_caution")
        self.assertEqual(lactation["status"], "unknown")
        self.assertIn("확인", lactation["summary"])
        self.assertIsNotNone(preview["warning_token"])

    def test_runtime_manifest_fails_closed_on_persisted_link_ambiguity(self) -> None:
        from medicine_app.canonical_runtime import canonical_manifest
        con = sqlite3.connect(self.canonical)
        con.execute(
            "UPDATE canonical_meta SET value=? WHERE key='unresolved_link_ambiguities'",
            ('[{"reason":"ambiguous"}]',),
        )
        con.commit()
        con.close()
        with closing(sqlite3.connect(self.canonical)) as check:
            self.assertEqual(canonical_manifest(check)["status"], "not_verified")


if __name__ == "__main__":
    unittest.main()
