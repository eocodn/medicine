from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from openpyxl import Workbook

from medicine_canonical.build import assemble_canonical_database, build_canonical_database, canonical_stats, verify_canonical_database
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_PAGE_SIZE_MAX, sync_canonical_api_sources
from medicine_canonical import linking as canonical_linking
from medicine_canonical.schema import SCHEMA


class CanonicalDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kids = self.root / "kids"
        self.kids.mkdir()
        self.db = self.root / "canonical.sqlite"
        self._write_all_xlsx()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _save_xlsx(self, filename: str, title: str, headers: list[str], rows: list[list[object]]) -> None:
        wb = Workbook()
        ws = wb.active
        ws.append([title] + [None] * (len(headers) - 1))
        ws.append(headers)
        for row in rows:
            ws.append(row)
        wb.save(self.kids / filename)

    def _write_all_xlsx(self) -> None:
        self._save_xlsx(
            "combination.xlsx", "병용금기 의약품 (2026.7.21. 고시 기준)",
            ["연번", "유효성분 '1'", "유효성분 '2'", "비고", "상세정보"],
            [[1, "alpha", "beta", None, "병용 상세"]],
        )
        self._save_xlsx(
            "age.xlsx", "특정연령대금기 의약품 (2026.7.21. 고시 기준)",
            ["연번", "성분명", "연령기준", "제형", "상세정보"],
            [[1, "alpha", "12세 미만", "정제", "연령 상세"]],
        )
        self._save_xlsx(
            "pregnancy.xlsx", "임부금기 의약품 (2026.7.21. 고시 기준)",
            ["연번", "성분명", "임부금기(등급)", "비고", "상세정보"],
            [[1, "alpha", "2등급", None, "임부 상세"]],
        )
        self._save_xlsx(
            "dose.xlsx", "용량주의 (2026.6.9. 공고 기준)",
            ["연번", "성분명(국문)", "성분명(영문)", "제형", "1일 최대용량", "비고"],
            [[1, "알파", "Alpha", "정제", "알파 240mg", None]],
        )
        self._save_xlsx(
            "duration.xlsx", "투여기간주의 (2026.6.9. 공고 기준)",
            ["연번", "성분명(국문)", "성분명(영문)", "제형", "최대 투여기간", "비고"],
            [[1, "알파", "Alpha", "정제", "28일", None]],
        )
        self._save_xlsx(
            "elderly.xlsx", "노인주의 (2025.05.30. 공고 기준)",
            ["연번", "성분명(국문)", "성분명(영문)", "제형", "비고"],
            [[1, "알파", "Alpha", "정제", None]],
        )
        self._save_xlsx(
            "therapeutic_duplication.xlsx", "효능군중복주의 (2026.6.9. 공고 기준)",
            ["연번", "효능군", "연번_2", "계열명", "연번_3", "성분명(국문)", "성분명(영문)", "비고"],
            [[1, "진통제", "1-1", "NSAID", "1-1-1", "알파", "Alpha", None]],
        )
        self._save_xlsx(
            "lactation.xlsx", "수유부주의 (2025.11.27. 공고 기준)",
            ["연번", "성분명(국문)", "성분명(영문)", "비고"],
            [[1, "알파", "Alpha", "수유 주의"]],
        )

    @staticmethod
    def _permit_fetch(page: int, page_size: int):
        if page > 1:
            return [], 2
        return [
            {
                "ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "ENTP_NAME": "회사A",
                "ITEM_INGR_NAME": "Alpha", "EDI_CODE": "E1", "ITEM_PERMIT_DATE": "20200101",
                "CANCEL_NAME": "정상",
            },
            {
                "ITEM_SEQ": "P2", "ITEM_NAME": "베타정", "ENTP_NAME": "회사B",
                "ITEM_INGR_NAME": "Beta", "ITEM_PERMIT_DATE": "20200102", "CANCEL_NAME": "정상",
            },
        ], 2

    @staticmethod
    def _dur_fetch(operation: str, page: int, page_size: int):
        if page > 1:
            return [], 1
        rows = {
            "getUsjntTabooInfoList03": [{
                "ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파",
                "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha",
                "MIXTURE_ITEM_SEQ": "P2", "MIXTURE_ITEM_NAME": "베타정",
                "MIXTURE_INGR_KOR_NAME": "베타", "MIXTURE_INGR_CODE": "D-BETA",
                "MIXTURE_INGR_ENG_NAME": "Beta", "PROHBT_CONTENT": "병용 금기",
                "NOTIFICATION_DATE": "20260721", "CHANGE_DATE": "20260721",
            }],
            "getSpcifyAgrdeTabooInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha", "PROHBT_CONTENT": "연령 금기"}],
            "getPwnmTabooInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha", "PROHBT_CONTENT": "임부 금기"}],
            "getCpctyAtentInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha Hydrochloride"}],
            "getMdctnPdAtentInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha"}],
            "getOdsnAtentInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha"}],
            "getEfcyDplctInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "INGR_NAME": "알파", "INGR_CODE": "D-ALPHA", "INGR_ENG_NAME": "Alpha", "EFFECT_NAME": "진통제"}],
            "getDurPrdlstInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "TYPE_CODE": "C,I", "TYPE_NAME": "임부금기,첨가제주의"}],
            "getSeobangjeongPartitnAtentInfoList03": [{"ITEM_SEQ": "P1", "ITEM_NAME": "알파정", "PROHBT_CONTENT": "분할불가", "FORM_CODE_NAME": "서방정"}],
        }
        return rows[operation], 1

    def _build(self):
        return build_canonical_database(
            self.db,
            self.kids,
            service_key="test-key",
            progress=False,
            permit_fetch_page=self._permit_fetch,
            dur_fetch_page=self._dur_fetch,
            api_workers=1,
        )

    def test_builds_three_source_family_canonical_database(self) -> None:
        result = self._build()
        self.assertEqual(result["products"], 2)
        self.assertEqual(result["product_rules"], 7)
        self.assertEqual(result["product_flags"], 3)
        self.assertEqual(result["ingredient_rules"], 8)
        self.assertEqual(result["product_criterion_links"], 7)
        self.assertEqual(result["linked_product_rules"], 7)
        self.assertEqual(result["product_ingredient_criterion_links"], 1)
        self.assertEqual(result["linked_product_ingredient_items"], 1)
        self.assertEqual(result["source_snapshots"], 18)

        with closing(sqlite3.connect(self.db)) as con:
            families = {row[0] for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")}
            self.assertEqual(families, {"mfds_permit_api", "mfds_dur_item_api", "kids_mfds_xlsx"})
            self.assertEqual(
                con.execute("SELECT paired_item_seq FROM product_rules WHERE category='combination_contraindication'").fetchone()[0],
                "P2",
            )
            dose = con.execute("SELECT rule_value,dosage_form FROM ingredient_rules WHERE category='dose_caution'").fetchone()
            self.assertEqual(dose, ("알파 240mg", "정제"))
            structured_dose = con.execute(
                """SELECT maximum_daily_amount,maximum_daily_unit,parse_status,parse_reason
                   FROM dose_criteria"""
            ).fetchone()
            self.assertEqual(structured_dose, ("240", "mg", "parsed", None))
            lactation = con.execute("SELECT ingredient_name,ingredient_name_ko,note FROM ingredient_rules WHERE category='lactation_caution'").fetchone()
            self.assertEqual(lactation, ("Alpha", "알파", "수유 주의"))
            identifiers = set(con.execute("SELECT system,value FROM product_identifiers WHERE item_seq='P1'").fetchall())
            self.assertEqual(identifiers, {("MFDS_ITEM_SEQ", "P1"), ("EDI", "E1")})
            dose_product = con.execute(
                "SELECT ingredient_code,ingredient_name_en FROM product_rules WHERE category='dose_caution'"
            ).fetchone()
            self.assertEqual(dose_product, ("D-ALPHA", "Alpha Hydrochloride"))
            dose_link = con.execute(
                """SELECT item_seq,criterion_rule_value,criterion_maximum_daily_amount,
                          criterion_maximum_daily_unit,criterion_dose_parse_status,match_method
                   FROM product_rule_criteria WHERE category='dose_caution'"""
            ).fetchone()
            self.assertEqual(
                dose_link,
                ("P1", "알파 240mg", "240", "mg", "parsed", "ingredient_preprocessed"),
            )
            combination_link = con.execute(
                """SELECT item_seq,paired_item_seq,match_method,pair_orientation
                   FROM product_rule_criteria WHERE category='combination_contraindication'"""
            ).fetchone()
            self.assertEqual(combination_link, ("P1", "P2", "english_exact", "forward"))
            lactation_link = con.execute(
                """SELECT item_seq,criterion_ingredient_name,match_method
                   FROM product_ingredient_criteria WHERE category='lactation_caution'"""
            ).fetchone()
            self.assertEqual(lactation_link, ("P1", "Alpha", "dur_scope_signature"))

    def test_verify_rejects_zero_quantitative_dose_coverage(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                """UPDATE dose_criteria
                   SET maximum_daily_amount=NULL,maximum_daily_unit=NULL,
                       parse_status='not_evaluable',parse_reason='forced test gap'"""
            )
            con.commit()

        verification = verify_canonical_database(self.db)
        self.assertEqual(verification["status"], "invalid")
        self.assertIn("no quantitative dose criteria are parsable", verification["errors"])
        self.assertIn("linked dose-caution products have zero quantitative evaluability", verification["errors"])
        self.assertIn("active linked dose-caution products have zero quantitative evaluability", verification["errors"])

    def test_reassemble_rejects_tampered_api_snapshot(self) -> None:
        self._build()
        raw_dir = self.db.parent / f"{self.db.stem}.sources"
        target = raw_dir / "dur_age.jsonl"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            assemble_canonical_database(self.root / "tampered.sqlite", self.kids, raw_dir)

    def test_can_reassemble_from_preserved_api_snapshots_without_network(self) -> None:
        self._build()
        raw_dir = self.db.parent / f"{self.db.stem}.sources"
        self.db.unlink()
        result = assemble_canonical_database(self.db, self.kids, raw_dir)
        self.assertEqual(result["products"], 2)
        with closing(sqlite3.connect(self.db)) as con:
            product_rule_cols = {row[1] for row in con.execute("PRAGMA table_info(product_rules)")}
            self.assertNotIn("raw_json", product_rule_cols)
            self.assertNotIn("product_name", product_rule_cols)
            self.assertEqual(con.execute("SELECT source_row FROM products WHERE item_seq='P1'").fetchone()[0], 1)
            snapshot_path = con.execute("SELECT snapshot_path FROM source_snapshots WHERE dataset_key='mfds_permit:products'").fetchone()[0]
            self.assertTrue(snapshot_path.endswith("mfds_permit_products.jsonl"))

    def test_rebuild_is_atomic_and_idempotent(self) -> None:
        first = self._build()
        second = self._build()
        self.assertEqual(first["products"], second["products"])
        self.assertEqual(first["product_rules"], second["product_rules"])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM products").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0], 18)

    def test_verify_rejects_non_core_source_family(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                "INSERT INTO source_snapshots(dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
                ("legacy:test", "legacy_product_csv", "legacy.csv", "legacy.csv", 1, "0" * 64, "{}"),
            )
            con.commit()
        result = verify_canonical_database(self.db)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("unsupported source families", " ".join(result["errors"]))

    def test_cli_stats_and_verify_are_machine_readable(self) -> None:
        self._build()
        for args in (["stats", "--db", str(self.db), "--json"], ["verify", "--db", str(self.db), "--json"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = canonical_main(args)
            self.assertEqual(code, 0)
            self.assertIn('"db_path"', buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = canonical_main([
                "criteria", "--db", str(self.db), "--item-seq", "P1",
                "--category", "dose_caution", "--json",
            ])
        self.assertEqual(code, 0)
        self.assertIn('"criterion_rule_value": "알파 240mg"', buf.getvalue())

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = canonical_main([
                "ingredient-criteria", "--db", str(self.db), "--item-seq", "P1",
                "--category", "lactation_caution", "--json",
            ])
        self.assertEqual(code, 0)
        self.assertIn('"criterion_ingredient_name": "Alpha"', buf.getvalue())
        self.assertIn('"match_method": "dur_scope_signature"', buf.getvalue())

    def test_mfds_api_page_limits_are_enforced(self) -> None:
        self.assertEqual(PERMIT_PAGE_SIZE_MAX, 500)
        with self.assertRaisesRegex(ValueError, "permit_page_size"):
            sync_canonical_api_sources(
                self.root / "raw-limit",
                service_key="test-key",
                permit_page_size=501,
                progress=False,
                permit_fetch_page=self._permit_fetch,
                dur_fetch_page=self._dur_fetch,
            )

    def test_link_code_preprocessing_has_only_four_explicit_equivalences(self) -> None:
        cases = {
            "D001289": "D000274",  # Ketorolac
            "D000195": "D000982",  # Naproxen
            "D000983": "D000309",  # Piroxicam
            "D000904": "D000719",  # Mizolastine
        }
        for raw_code, expected in cases.items():
            self.assertEqual(canonical_linking.canonicalize_link_ingredient_code(raw_code), expected)
        self.assertEqual(canonical_linking.canonicalize_link_ingredient_code("D999999"), "D999999")

    def test_ketorolac_alias_links_without_mutating_raw_mfds_code(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(SCHEMA)
            for key, family in (("mfds", "mfds_dur_item_api"), ("xlsx", "kids_mfds_xlsx")):
                con.execute(
                    "INSERT INTO source_snapshots(dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
                    (key, family, key, key, 1, "0" * 64, "{}"),
                )
            con.execute(
                """INSERT INTO product_rules(
                    source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name_en,
                    paired_item_seq,paired_ingredient_code,paired_ingredient_name_en
                ) VALUES('mfds',1,'combination_contraindication','P1','D000274','Ketorolac Tromethamine','P2','D-BETA','Beta')"""
            )
            con.execute(
                """INSERT INTO product_rules(
                    source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name_en
                ) VALUES('mfds',2,'elderly_caution','P1','D001289','Ketorolac')"""
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                    source_dataset_key,source_row,category,ingredient_name,paired_ingredient_name
                ) VALUES('xlsx',1,'combination_contraindication','Ketorolac','Beta')"""
            )
            result = canonical_linking.materialize_product_criterion_links(con)
            self.assertEqual(result["product_criterion_links"], 1)
            self.assertEqual(result["unresolved_link_ambiguities"], [])
            self.assertEqual(
                con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0],
                "mfds_ingredient_code",
            )
            self.assertEqual(
                con.execute("SELECT ingredient_code FROM product_rules WHERE category='elderly_caution'").fetchone()[0],
                "D001289",
            )
        finally:
            con.close()

    def test_new_unhandled_code_ambiguity_is_reported(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(SCHEMA)
            for key, family in (("mfds", "mfds_dur_item_api"), ("xlsx", "kids_mfds_xlsx")):
                con.execute(
                    "INSERT INTO source_snapshots(dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
                    (key, family, key, key, 1, "0" * 64, "{}"),
                )
            con.executemany(
                """INSERT INTO product_rules(
                    source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name_en,
                    paired_item_seq,paired_ingredient_code,paired_ingredient_name_en
                ) VALUES('mfds',?,?,?,?,?,?,?,?)""",
                [
                    (1, 'combination_contraindication', 'P1', 'D-FUTURE-1', 'FutureDrug Salt', 'P2', 'D-BETA', 'Beta'),
                    (2, 'age_contraindication', 'P3', 'D-FUTURE-1', 'FutureDrug', None, None, None),
                    (3, 'pregnancy_contraindication', 'P4', 'D-FUTURE-2', 'FutureDrug', None, None, None),
                ],
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                    source_dataset_key,source_row,category,ingredient_name,paired_ingredient_name
                ) VALUES('xlsx',1,'combination_contraindication','FutureDrug','Beta')"""
            )
            result = canonical_linking.materialize_product_criterion_links(con)
            self.assertEqual(result["product_criterion_links"], 0)
            self.assertEqual(
                result["unresolved_link_ambiguities"],
                [{
                    "category": "combination_contraindication",
                    "ingredient_name": "FutureDrug",
                    "candidate_codes": ["D-FUTURE-1", "D-FUTURE-2"],
                    "reason": "active_moiety_multiple_codes",
                }],
            )
        finally:
            con.close()

    def test_verify_rejects_persisted_unhandled_link_ambiguity(self) -> None:
        self._build()
        ambiguity = [{
            "category": "combination_contraindication",
            "ingredient_name": "FutureDrug",
            "candidate_codes": ["D-FUTURE-1", "D-FUTURE-2"],
        }]
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                "UPDATE canonical_meta SET value=? WHERE key='unresolved_link_ambiguities'",
                (json.dumps(ambiguity),),
            )
            con.commit()
        stats = canonical_stats(self.db)
        self.assertEqual(stats["unresolved_link_ambiguity_count"], 1)
        self.assertEqual(stats["unresolved_link_ambiguities"], ambiguity)
        verification = verify_canonical_database(self.db)
        self.assertEqual(verification["status"], "invalid")
        self.assertIn("FutureDrug", " ".join(verification["errors"]))

    def test_verify_rejects_resolved_and_unresolved_ingredient_overlap(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            criterion_id = con.execute(
                "SELECT criterion_rule_id FROM product_ingredient_criterion_links LIMIT 1"
            ).fetchone()[0]
            con.execute(
                """INSERT INTO product_ingredient_criterion_unresolved(
                       item_seq,criterion_rule_id,category,reason,evidence_json
                   ) VALUES('P1',?,'lactation_caution','scope_relation_unproven','{}')""",
                (criterion_id,),
            )
            con.commit()
        verification = verify_canonical_database(self.db)
        self.assertEqual(verification["status"], "invalid")
        self.assertIn("resolved/unresolved ingredient applicability overlap", " ".join(verification["errors"]))

    def test_declares_all_expected_live_dur_endpoints(self) -> None:
        self.assertEqual(len(DUR_ENDPOINTS), 9)
        self.assertEqual(
            {spec.category for spec in DUR_ENDPOINTS.values() if spec.kind == "rule"},
            {
                "combination_contraindication", "age_contraindication", "pregnancy_contraindication",
                "dose_caution", "duration_caution", "elderly_caution", "therapeutic_duplication_caution",
            },
        )


if __name__ == "__main__":
    unittest.main()
