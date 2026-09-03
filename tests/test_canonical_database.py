from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from medicine_canonical import linking as canonical_linking
from medicine_canonical.build import (
    assemble_canonical_database,
    build_canonical_database,
    canonical_stats,
    sync_reference_sources,
    verify_canonical_database,
)
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mfds_ingredient import (
    MFDS_INGREDIENT_ENDPOINTS,
    MFDS_INGREDIENT_PAGE_SIZE_MAX,
)
from medicine_canonical.mfds_sync import request_json, sync_paginated_jsonl
from medicine_canonical.schema import SCHEMA
from medicine_canonical.source_layout import MfdsSourceLayout
from medicine_canonical.source_policy import (
    CANONICAL_SOURCE_POLICY,
    EXPECTED_CANONICAL_SOURCE_FAMILIES,
    EXPECTED_CANONICAL_SOURCE_KEYS,
)
from medicine_canonical.sources import (
    DUR_ENDPOINTS,
    PERMIT_DATASET_KEY,
    PERMIT_PAGE_SIZE_MAX,
    sync_canonical_api_sources,
)

class CanonicalDatabaseTestFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "canonical.sqlite"
    def tearDown(self) -> None:
        self.tmp.cleanup()
    @staticmethod
    def _permit_fetch(page: int, page_size: int):
        if page > 1:
            return [], 2
        return [
            {
                "ITEM_SEQ": "P1",
                "ITEM_NAME": "알파정",
                "ENTP_NAME": "회사A",
                "ITEM_INGR_NAME": "Alpha",
                "EDI_CODE": "E1",
                "FORM_CODE_NAME": "정제",
                "ITEM_PERMIT_DATE": "20200101",
                "CANCEL_NAME": "정상",
            },
            {
                "ITEM_SEQ": "P2",
                "ITEM_NAME": "베타정",
                "ENTP_NAME": "회사B",
                "ITEM_INGR_NAME": "Beta",
                "FORM_CODE_NAME": "정제",
                "ITEM_PERMIT_DATE": "20200102",
                "CANCEL_NAME": "정상",
            },
        ], 2
    @staticmethod
    def _dur_fetch(operation: str, page: int, page_size: int):
        if page > 1:
            return [], 1
        single = {
            "ITEM_SEQ": "P1",
            "ITEM_NAME": "알파정",
            "INGR_NAME": "알파",
            "INGR_CODE": "D-ALPHA",
            "INGR_ENG_NAME": "Alpha",
            "FORM_NAME": "정제",
        }
        rows = {
            "getUsjntTabooInfoList03": [{
                **single,
                "MIXTURE_ITEM_SEQ": "P2",
                "MIXTURE_ITEM_NAME": "베타정",
                "MIXTURE_INGR_KOR_NAME": "베타",
                "MIXTURE_INGR_CODE": "D-BETA",
                "MIXTURE_INGR_ENG_NAME": "Beta",
                "PROHBT_CONTENT": "병용 금기",
                "NOTIFICATION_DATE": "20260721",
                "CHANGE_DATE": "20260721",
            }],
            "getSpcifyAgrdeTabooInfoList03": [{**single, "PROHBT_CONTENT": "연령 금기"}],
            "getPwnmTabooInfoList03": [{**single, "PROHBT_CONTENT": "임부 금기"}],
            "getCpctyAtentInfoList03": [{
                **single,
                "INGR_ENG_NAME": "Alpha Hydrochloride",
                "PROHBT_CONTENT": "240밀리그램",
            }],
            "getMdctnPdAtentInfoList03": [{**single, "PROHBT_CONTENT": "28일"}],
            "getOdsnAtentInfoList03": [{**single, "PROHBT_CONTENT": "노인 주의"}],
            "getEfcyDplctInfoList03": [{**single, "EFFECT_NAME": "진통제"}],
            "getDurPrdlstInfoList03": [{
                "ITEM_SEQ": "P1",
                "ITEM_NAME": "알파정",
                "TYPE_CODE": "C,I",
                "TYPE_NAME": "임부금기,첨가제주의",
            }],
            "getSeobangjeongPartitnAtentInfoList03": [{
                "ITEM_SEQ": "P1",
                "ITEM_NAME": "알파정",
                "PROHBT_CONTENT": "분할불가",
                "FORM_CODE_NAME": "서방정",
            }],
        }
        return rows[operation], 1
    @staticmethod
    def _ingredient_fetch(operation: str, page: int, page_size: int):
        if page > 1:
            return [], 2
        common = {
            "DUR_SEQ": "101",
            "INGR_CODE": "D-ALPHA",
            "INGR_ENG_NAME": "Alpha",
            "INGR_NAME": "알파",
            "FORM_NAME": "정제",
            "NOTIFICATION_DATE": "20260721",
            "PROHBT_CONTENT": "상세 주의",
            "REMARK": "",
            "DEL_YN": "정상",
            "MIX_TYPE": "단일",
            "MIX_INGR": "",
        }
        if operation == "getUsjntTabooInfoList02":
            common.update({
                "MIXTURE_INGR_CODE": "D-BETA",
                "MIXTURE_INGR_ENG_NAME": "Beta",
                "MIXTURE_INGR_KOR_NAME": "베타",
            })
        elif operation == "getSpcifyAgrdeTabooInfoList02":
            common["AGE_BASE"] = "12세 미만"
        elif operation == "getPwnmTabooInfoList02":
            common["GRADE"] = "2등급"
        elif operation == "getCpctyAtentInfoList02":
            common["MAX_QTY"] = "240밀리그램"
        elif operation == "getMdctnPdAtentInfoList02":
            common["MAX_DOSAGE_TERM"] = "28일"
        elif operation == "getEfcyDplctInfoList02":
            common["EFFECT_CODE"] = "진통제"
            common["SERS_NAME"] = "NSAID"
        active = common
        deleted = {**common, "DUR_SEQ": "999", "DEL_YN": "삭제"}
        return [active, deleted], 2
    def _build(self):
        return build_canonical_database(
            self.db,
            service_key="test-key",
            progress=False,
            permit_fetch_page=self._permit_fetch,
            dur_fetch_page=self._dur_fetch,
            ingredient_fetch_page=self._ingredient_fetch,
            api_workers=1,
        )

class CanonicalDatabaseTest(CanonicalDatabaseTestFixture):
    def test_assemble_resumes_verified_source_stage_from_input_bound_checkpoint(self) -> None:
        self._build()
        self.db.unlink()
        layout = MfdsSourceLayout.for_database(self.db)
        events: list[dict[str, object]] = []

        with mock.patch(
            "medicine_canonical.build.materialize_product_search_documents",
            side_effect=RuntimeError("materialization interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "materialization interrupted"):
                assemble_canonical_database(self.db, layout, progress=events.append)

        checkpoint = self.db.with_name(self.db.name + ".build.checkpoint.json")
        staged = self.db.with_name(self.db.name + ".tmp")
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(staged.is_file())
        self.assertTrue(any(event["status"] == "checkpoint" for event in events))
        self.assertTrue(any(event["status"] == "failed" for event in events))

        with mock.patch(
            "medicine_canonical.build.populate_canonical_source_tables",
            wraps=__import__("medicine_canonical.build", fromlist=["populate_canonical_source_tables"]).populate_canonical_source_tables,
        ) as populate:
            result = assemble_canonical_database(self.db, layout, progress=events.append)

        self.assertEqual(populate.call_count, 0)
        self.assertGreater(result["products"], 0)
        self.assertFalse(checkpoint.exists())
        self.assertFalse(staged.exists())
    def test_assemble_rejects_mutated_source_stage_checkpoint(self) -> None:
        self._build()
        self.db.unlink()
        layout = MfdsSourceLayout.for_database(self.db)

        with mock.patch(
            "medicine_canonical.build.materialize_product_search_documents",
            side_effect=RuntimeError("materialization interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "materialization interrupted"):
                assemble_canonical_database(self.db, layout)

        checkpoint = self.db.with_name(self.db.name + ".build.checkpoint.json")
        staged = self.db.with_name(self.db.name + ".tmp")
        with closing(sqlite3.connect(staged)) as con, con:
            con.execute("UPDATE products SET product_name='TAMPERED' WHERE item_seq='P1'")

        with self.assertRaisesRegex(RuntimeError, "checkpoint.*bytes changed"):
            assemble_canonical_database(self.db, layout)

        self.assertFalse(checkpoint.exists())
        self.assertFalse(staged.exists())
    def test_assemble_rejects_mutated_materialized_checkpoint(self) -> None:
        self._build()
        self.db.unlink()
        layout = MfdsSourceLayout.for_database(self.db)

        with mock.patch(
            "medicine_canonical.build.verify_canonical_database",
            side_effect=RuntimeError("verification interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "verification interrupted"):
                assemble_canonical_database(self.db, layout)

        checkpoint = self.db.with_name(self.db.name + ".build.checkpoint.json")
        staged = self.db.with_name(self.db.name + ".tmp")
        with closing(sqlite3.connect(staged)) as con, con:
            con.execute("UPDATE products SET product_name='TAMPERED' WHERE item_seq='P1'")

        with self.assertRaisesRegex(RuntimeError, "checkpoint.*bytes changed"):
            assemble_canonical_database(self.db, layout)

        self.assertFalse(checkpoint.exists())
        self.assertFalse(staged.exists())
    def test_cli_build_exposes_structured_job_progress_and_checkpoint_events(self) -> None:
        self._build()
        self.db.unlink()
        layout = MfdsSourceLayout.for_database(self.db)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = canonical_main(
                [
                    "build",
                    "--db",
                    str(self.db),
                    "--raw-dir",
                    str(layout.product_dir),
                    "--ingredient-raw-dir",
                    str(layout.ingredient_dir),
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        events = [
            json.loads(line)["canonical_progress"]
            for line in stderr.getvalue().splitlines()
            if line.strip()
        ]
        self.assertEqual(events[0]["status"], "started")
        self.assertTrue(any(event["status"] == "checkpoint" for event in events))
        self.assertTrue(any(event.get("bar", "").startswith("[") for event in events))
        self.assertEqual(events[-1]["status"], "completed")
    def test_rejects_unknown_product_flag_code(self) -> None:
        def fetch(operation: str, page: int, page_size: int):
            if operation == "getDurPrdlstInfoList03":
                return ([{
                    "ITEM_SEQ": "P1",
                    "ITEM_NAME": "알파정",
                    "TYPE_CODE": "Z",
                    "TYPE_NAME": "알수없음",
                }], 1)
            return self._dur_fetch(operation, page, page_size)

        with self.assertRaisesRegex(ValueError, "unsupported DUR product flag code 'Z'"):
            build_canonical_database(
                self.db,
                service_key="test-key",
                progress=False,
                permit_fetch_page=self._permit_fetch,
                dur_fetch_page=fetch,
                ingredient_fetch_page=self._ingredient_fetch,
                api_workers=1,
            )
    def test_builds_mfds_only_canonical_database(self) -> None:
        result = self._build()
        self.assertEqual(result["products"], 2)
        self.assertEqual(result["product_rules"], 7)
        self.assertEqual(result["product_flags"], 2)
        self.assertEqual(result["ingredient_rules"], 7)
        self.assertEqual(result["product_criterion_links"], 7)
        self.assertEqual(result["linked_product_rules"], 7)
        self.assertEqual(result["source_snapshots"], 17)
        self.assertEqual(result["ingredient_deleted_rows_skipped"], 7)

        with closing(sqlite3.connect(self.db)) as con:
            families = {
                row[0] for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")
            }
            self.assertEqual(
                families,
                {"mfds_permit_api", "mfds_dur_item_api", "mfds_dur_ingredient_api"},
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM source_snapshots WHERE dataset_key LIKE 'kids%'")
                .fetchone()[0],
                0,
            )
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM ingredient_rule_codes").fetchone()[0], 7
            )
            self.assertEqual(
                con.execute(
                    "SELECT rule_value FROM ingredient_rules WHERE category='dose_caution'"
                ).fetchone()[0],
                "240밀리그램",
            )
            self.assertEqual(
                con.execute(
                    """SELECT maximum_daily_amount,maximum_daily_unit,parse_status
                       FROM dose_criteria"""
                ).fetchone(),
                ("240", "mg", "parsed"),
            )
            self.assertEqual(
                con.execute(
                    """SELECT criterion_rule_value,match_method
                       FROM product_rule_criteria WHERE category='dose_caution'"""
                ).fetchone(),
                ("240밀리그램", "mfds_ingredient_code"),
            )
            self.assertEqual(
                con.execute(
                    """SELECT pair_orientation,match_method
                       FROM product_rule_criteria
                       WHERE category='combination_contraindication'"""
                ).fetchone(),
                ("forward", "mfds_ingredient_code"),
            )
            duplication = con.execute(
                """SELECT note,qualifier_note FROM ingredient_rules
                   WHERE category='therapeutic_duplication_caution'"""
            ).fetchone()
            self.assertEqual(duplication, ("NSAID", None))
            meta = dict(con.execute("SELECT key,value FROM canonical_meta"))
            self.assertEqual(meta["source_policy"], CANONICAL_SOURCE_POLICY)
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
    def test_verify_rejects_unreviewed_mfds_remark(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                "UPDATE ingredient_rules SET qualifier_note=? WHERE category='pregnancy_contraindication'",
                ("새로 추가된 미검토 비고",),
            )
            con.commit()
        verification = verify_canonical_database(self.db)
        self.assertEqual(verification["status"], "invalid")
        self.assertIn("unreviewed MFDS REMARK", " ".join(verification["errors"]))
    def test_can_reassemble_from_preserved_mfds_snapshots_without_network(self) -> None:
        self._build()
        raw_dir = self.root / "canonical.sources"
        ingredient_raw_dir = self.root / "mfds_ingredient"
        self.db.unlink()
        result = assemble_canonical_database(
            self.db, MfdsSourceLayout.from_roots(raw_dir, ingredient_raw_dir)
        )
        self.assertEqual(result["products"], 2)
        self.assertEqual(result["source_snapshots"], 17)
    def test_reassemble_rejects_tampered_api_snapshot(self) -> None:
        self._build()
        raw_dir = self.root / "canonical.sources"
        ingredient_raw_dir = self.root / "mfds_ingredient"
        target = ingredient_raw_dir / MFDS_INGREDIENT_ENDPOINTS["getSpcifyAgrdeTabooInfoList02"].filename
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            assemble_canonical_database(
                self.root / "tampered.sqlite",
                MfdsSourceLayout.from_roots(raw_dir, ingredient_raw_dir),
            )
    def test_rebuild_is_atomic_and_idempotent(self) -> None:
        first = self._build()
        second = self._build()
        self.assertEqual(first["products"], second["products"])
        self.assertEqual(first["product_rules"], second["product_rules"])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0], 17)
    def test_verify_rejects_non_core_source_family(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?)""",
                ("unsupported:test", "unsupported_source", "unsupported.dat", "unsupported.dat", 1, "0" * 64, "{}"),
            )
            con.commit()
        result = verify_canonical_database(self.db)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("unsupported source families", " ".join(result["errors"]))
    def test_cli_stats_verify_and_criteria_are_machine_readable(self) -> None:
        self._build()
        for args in (
            ["stats", "--db", str(self.db), "--json"],
            ["verify", "--db", str(self.db), "--json"],
            [
                "criteria",
                "--db",
                str(self.db),
                "--item-seq",
                "P1",
                "--category",
                "dose_caution",
                "--json",
            ],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = canonical_main(args)
            self.assertEqual(code, 0)
            self.assertTrue(buf.getvalue().strip().startswith(('{', '[')))
