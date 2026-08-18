from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.source_policy import CANONICAL_SOURCE_POLICY, EXPECTED_CANONICAL_SOURCE_FAMILIES
from medicine_app.canonical_runtime import _RUNTIME_SOURCE_FAMILIES
from tests.canonical_fixture_support import expected_source_snapshots
from medicine_app.core import MedicationApp
from medicine_app.products import ProductRepository


def make_canonical_runtime_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        snapshots = expected_source_snapshots()
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
                ("source_policy", CANONICAL_SOURCE_POLICY),
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
                (1, "mfds_dur_ingredient:getMdctnPdAtentInfoList02", 11, "duration_caution", "Zolpidem", "졸피뎀", None, "28일", "정제", None, "최대 28일", "1"),
                (2, "mfds_dur_ingredient:getUsjntTabooInfoList02", 12, "combination_contraindication", "Zolpidem", "졸피뎀", "Itraconazole", None, None, None, "병용금기", "2"),
            ],
        )
        con.executemany(
            """INSERT INTO ingredient_rule_codes(
                   criterion_rule_id,ingredient_code,paired_ingredient_code,mixture_type,
                   mixture_ingredient_codes_json,mixture_ingredient_names_json
               ) VALUES(?,?,?,?,?,?)""",
            [
                (1, "D-Z", None, "단일", "[]", "[]"),
                (2, "D-Z", "D-I", None, "[]", "[]"),
            ],
        )
        con.executemany(
            "INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id,match_method,pair_orientation) VALUES(?,?,?,?)",
            [(1, 1, "mfds_ingredient_code", None), (2, 2, "mfds_ingredient_code", "forward")],
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

    def test_app_uses_canonical_product_criteria_without_lactation_support(self) -> None:
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
        self.assertNotIn("lactation_caution", by_category)
        self.assertEqual(by_category["duration_caution"]["status"], "hit")
        self.assertEqual(preview["coverage"]["product"]["identity_method"], "item_seq_exact")
        self.assertEqual(preview["coverage"]["dataset"]["status"], "verified")

    def test_lactation_specific_reference_tables_are_absent(self) -> None:
        with closing(sqlite3.connect(self.canonical)) as con:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("product_ingredient_criterion_links", names)
        self.assertNotIn("product_ingredient_criterion_unresolved", names)
        self.assertNotIn("product_ingredient_criteria", names)

    def test_runtime_source_policy_matches_release_policy(self) -> None:
        self.assertEqual(_RUNTIME_SOURCE_FAMILIES, EXPECTED_CANONICAL_SOURCE_FAMILIES)

    def test_runtime_manifest_requires_exact_source_snapshot_set(self) -> None:
        from medicine_app.canonical_runtime import canonical_manifest
        with closing(sqlite3.connect(self.canonical)) as con:
            con.execute("DELETE FROM source_snapshots WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'")
            con.commit()
            manifest = canonical_manifest(con)
        self.assertEqual(manifest["status"], "not_verified")



if __name__ == "__main__":
    unittest.main()
