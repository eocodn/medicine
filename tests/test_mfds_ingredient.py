from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mfds_ingredient import (
    MFDS_INGREDIENT_ENDPOINTS,
    assemble_mfds_ingredient_preview,
    import_mfds_ingredient_snapshots,
    sync_mfds_ingredient_sources,
)
from medicine_canonical.schema import SCHEMA


class MfdsIngredientCanonicalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.db = self.root / "mfds-ingredient.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _active_row(operation: str) -> dict:
        common = {
            "DUR_SEQ": "101",
            "INGR_CODE": "D000101",
            "INGR_ENG_NAME": "Alpha",
            "INGR_NAME": "알파",
            "FORM_NAME": "정제",
            "NOTIFICATION_DATE": "20260721",
            "PROHBT_CONTENT": "상세 주의",
            "REMARK": "비고",
            "DEL_YN": "정상",
            "MIX_TYPE": "단일",
            "MIX_INGR": "",
        }
        if operation == "getUsjntTabooInfoList02":
            common.update(
                {
                    "INGR_KOR_NAME": "알파",
                    "MIXTURE_INGR_CODE": "D000202",
                    "MIXTURE_INGR_ENG_NAME": "Beta",
                    "MIXTURE_INGR_KOR_NAME": "베타",
                }
            )
        elif operation == "getSpcifyAgrdeTabooInfoList02":
            common["AGE_BASE"] = "12세 미만"
        elif operation == "getPwnmTabooInfoList02":
            common["GRADE"] = "2등급"
        elif operation == "getCpctyAtentInfoList02":
            common["MAX_QTY"] = "240밀리그램"
        elif operation == "getMdctnPdAtentInfoList02":
            common["MAX_DOSAGE_TERM"] = "28일"
        elif operation == "getEfcyDplctInfoList02":
            common["EFFECT_CODE"] = "해열진통소염제"
            common["SERS_NAME"] = "비스테로이드성 소염제"
        return common

    @classmethod
    def _fetch(cls, operation: str, page: int, page_size: int):
        if page > 1:
            return [], 2
        active = cls._active_row(operation)
        deleted = {**active, "DUR_SEQ": "999", "DEL_YN": "삭제"}
        return [active, deleted], 2

    def _sync(self) -> dict:
        return sync_mfds_ingredient_sources(
            self.raw,
            service_key="test-key",
            page_size=500,
            workers=1,
            progress=False,
            fetch_page=self._fetch,
        )

    @staticmethod
    def _refresh_snapshot_hash(path: Path) -> None:
        metadata_path = path.with_suffix(path.suffix + ".meta.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    def test_declares_seven_official_ingredient_endpoints(self) -> None:
        self.assertEqual(
            set(MFDS_INGREDIENT_ENDPOINTS),
            {
                "getUsjntTabooInfoList02",
                "getSpcifyAgrdeTabooInfoList02",
                "getPwnmTabooInfoList02",
                "getCpctyAtentInfoList02",
                "getMdctnPdAtentInfoList02",
                "getOdsnAtentInfoList02",
                "getEfcyDplctInfoList02",
            },
        )

    def test_sync_preserves_full_api_snapshot_with_distinct_provenance(self) -> None:
        result = self._sync()

        self.assertEqual(result["source_rows"], 14)
        self.assertEqual(len(result["sources"]), 7)
        for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items():
            path = self.raw / spec.filename
            metadata = json.loads(path.with_suffix(path.suffix + ".meta.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["dataset_key"], f"mfds_dur_ingredient:{operation}")
            self.assertEqual(metadata["source_family"], "mfds_dur_ingredient_api")
            self.assertEqual(metadata["row_count"], 2)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual({row["DEL_YN"] for row in rows}, {"정상", "삭제"})

    def test_preview_converts_only_active_rows_to_existing_canonical_rule_shape(self) -> None:
        self._sync()
        result = assemble_mfds_ingredient_preview(self.db, self.raw)

        self.assertEqual(result["source_snapshots"], 7)
        self.assertEqual(result["source_rows"], 14)
        self.assertEqual(result["ingredient_rules"], 7)
        self.assertEqual(result["deleted_rows_skipped"], 7)
        self.assertEqual(result["dose_criteria"], 1)
        self.assertEqual(result["status"], "built")

        with closing(sqlite3.connect(self.db)) as con:
            combination = con.execute(
                """SELECT i.sequence_text,i.ingredient_name,i.ingredient_name_ko,i.paired_ingredient_name,
                          i.dosage_form,i.note,i.qualifier_note,i.details,c.ingredient_code,c.paired_ingredient_code
                   FROM ingredient_rules i
                   JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id
                   WHERE i.category='combination_contraindication'"""
            ).fetchone()
            self.assertEqual(
                combination,
                ("101", "Alpha", "알파", "Beta", "정제", None, "비고", "상세 주의", "D000101", "D000202"),
            )

            code_rows = con.execute(
                "SELECT COUNT(*) FROM ingredient_rule_codes"
            ).fetchone()[0]
            self.assertEqual(code_rows, 7)

            age = con.execute(
                "SELECT rule_value FROM ingredient_rules WHERE category='age_contraindication'"
            ).fetchone()[0]
            pregnancy = con.execute(
                "SELECT rule_value FROM ingredient_rules WHERE category='pregnancy_contraindication'"
            ).fetchone()[0]
            dose = con.execute(
                "SELECT rule_value FROM ingredient_rules WHERE category='dose_caution'"
            ).fetchone()[0]
            duration = con.execute(
                "SELECT rule_value FROM ingredient_rules WHERE category='duration_caution'"
            ).fetchone()[0]
            duplication = con.execute(
                "SELECT rule_value,note,qualifier_note FROM ingredient_rules WHERE category='therapeutic_duplication_caution'"
            ).fetchone()
            self.assertEqual(age, "12세 미만")
            self.assertEqual(pregnancy, "2등급")
            self.assertEqual(dose, "240밀리그램")
            self.assertEqual(duration, "28일")
            self.assertEqual(duplication, ("해열진통소염제", "비스테로이드성 소염제", "비고"))

            source_families = {
                row[0] for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")
            }
            self.assertEqual(source_families, {"mfds_dur_ingredient_api"})
            meta = dict(con.execute("SELECT key,value FROM canonical_meta"))
            self.assertEqual(meta["source_policy"], "mfds_dur_ingredient_api")
            self.assertEqual(meta["build_stage"], "ingredient_preview")

    def test_import_preserves_authoritative_mixture_code_name_pairs(self) -> None:
        self._sync()
        path = self.raw / MFDS_INGREDIENT_ENDPOINTS["getCpctyAtentInfoList02"].filename
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["MIX_TYPE"] = "복합"
        rows[0]["MIX_INGR"] = (
            "[D000202]Beta(베타)/[D000303]Gamma Hydrochloride(감마염산염)"
        )
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        self._refresh_snapshot_hash(path)

        assemble_mfds_ingredient_preview(self.db, self.raw)

        with closing(sqlite3.connect(self.db)) as con:
            row = con.execute(
                """SELECT c.mixture_type,c.mixture_ingredient_codes_json,
                          c.mixture_ingredient_names_json
                   FROM ingredient_rule_codes c
                   JOIN ingredient_rules i ON i.id=c.criterion_rule_id
                   WHERE i.category='dose_caution'"""
            ).fetchone()
        self.assertEqual(row[0], "복합")
        self.assertEqual(json.loads(row[1]), ["D000202", "D000303"])
        self.assertEqual(json.loads(row[2]), ["Beta", "Gamma Hydrochloride"])

    def test_import_rejects_unknown_delete_state_instead_of_silently_using_it(self) -> None:
        self._sync()
        operation = "getOdsnAtentInfoList02"
        spec = MFDS_INGREDIENT_ENDPOINTS[operation]
        path = self.raw / spec.filename
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["DEL_YN"] = "보류"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        self._refresh_snapshot_hash(path)

        with closing(sqlite3.connect(":memory:")) as con:
            con.executescript(SCHEMA)
            with self.assertRaisesRegex(ValueError, "unsupported DEL_YN"):
                import_mfds_ingredient_snapshots(con, self.raw)

    def test_active_dose_row_without_max_qty_is_preserved_as_not_evaluable(self) -> None:
        self._sync()
        spec = MFDS_INGREDIENT_ENDPOINTS["getCpctyAtentInfoList02"]
        path = self.raw / spec.filename
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["MAX_QTY"] = None
        rows[0]["PROHBT_CONTENT"] = "해당 제형(점안제)으로 1방울"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        self._refresh_snapshot_hash(path)

        result = assemble_mfds_ingredient_preview(self.db, self.raw)
        self.assertEqual(result["ingredient_rules"], 7)
        with closing(sqlite3.connect(self.db)) as con:
            rule = con.execute(
                "SELECT rule_value,details FROM ingredient_rules WHERE category='dose_caution'"
            ).fetchone()
            structured = con.execute(
                "SELECT parse_status,parse_reason FROM dose_criteria"
            ).fetchone()
        self.assertEqual(rule, (None, "해당 제형(점안제)으로 1방울"))
        self.assertEqual(structured[0], "not_evaluable")
        self.assertEqual(structured[1], "dose criterion is missing")

    def test_cli_build_exposes_headless_preview_conversion(self) -> None:
        self._sync()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = canonical_main(
                [
                    "mfds-ingredient-build",
                    "--raw-dir",
                    str(self.raw),
                    "--db",
                    str(self.db),
                    "--json",
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "built")
        self.assertEqual(payload["ingredient_rules"], 7)


if __name__ == "__main__":
    unittest.main()