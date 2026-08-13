from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.product_flags import apply_product_flag_fallbacks, build_product_flag_checks
from medicine_dur.db import build_database, database_stats


class ProductItemFlagImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.kids = self.root / "kids"
        self.raw.mkdir()
        self.kids.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_jsonl(self, name: str, rows: list[dict]) -> None:
        (self.raw / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_imports_dur_item_flags_and_split_caution_as_verified_sources(self) -> None:
        self._write_jsonl(
            "dur_product_info.jsonl",
            [
                {
                    "ITEM_SEQ": "MFDS-1",
                    "ITEM_NAME": "테스트정",
                    "EDI_CODE": "P-1",
                    "TYPE_CODE": "C,I",
                    "TYPE_NAME  ": "임부금기,첨가제주의",
                    "CHANGE_DATE": "20260801",
                }
            ],
        )
        self._write_jsonl(
            "extended_release_split_caution.jsonl",
            [
                {
                    "ITEM_SEQ": "MFDS-1",
                    "ITEM_NAME": "테스트정",
                    "TYPE_NAME": "분할주의",
                    "FORM_CODE_NAME": "서방정",
                    "MAIN_INGR": "[M1]test",
                    "PROHBT_CONTENT": "분할불가",
                    "CHANGE_DATE": "20260802",
                }
            ],
        )
        db = self.root / "dur.sqlite"

        result = build_database(db, self.raw, self.kids, progress=False)

        self.assertEqual(result["source_files"], 2)
        self.assertEqual(result["product_flag_rows"], 3)
        stats = database_stats(db)
        self.assertEqual(stats["product_flag_rows"], 3)
        con = sqlite3.connect(db)
        try:
            rows = con.execute(
                """SELECT item_seq,category,flag_code,flag_name,details
                   FROM product_item_flags ORDER BY category"""
            ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("MFDS-1", "additive_caution", "I", "첨가제주의", None),
                    ("MFDS-1", "pregnancy_contraindication", "C", "임부금기", None),
                    ("MFDS-1", "split_caution", "S", "서방정분할주의", "분할불가"),
                ],
            )
            source_keys = {
                row[0] for row in con.execute("SELECT dataset_key FROM source_files")
            }
            self.assertEqual(
                source_keys,
                {"product_item:dur_product_info", "product_item:split_caution"},
            )
        finally:
            con.close()


class ProductFlagStatusTest(unittest.TestCase):
    def test_additive_and_split_flags_are_visible_product_level_warnings(self) -> None:
        checks = build_product_flag_checks(
            {
                "product_flags": [
                    {
                        "category": "additive_caution",
                        "flag_name": "첨가제주의",
                        "details": None,
                    },
                    {
                        "category": "split_caution",
                        "flag_name": "서방정분할주의",
                        "details": "분할불가",
                    },
                ]
            }
        )

        by_category = {item["category"]: item for item in checks}
        self.assertEqual(by_category["additive_caution"]["status"], "hit")
        self.assertEqual(by_category["additive_caution"]["summary"], "첨가제 주의사항 있음")
        self.assertEqual(by_category["split_caution"]["status"], "hit")
        self.assertIn("분할불가", by_category["split_caution"]["details"])

    def test_product_info_pregnancy_flag_becomes_hit_when_detail_row_is_missing(self) -> None:
        core = [{
            "category": "pregnancy_contraindication",
            "label": "임부금기",
            "status": "unknown",
            "summary": "자동 확인 제한",
            "findings": [],
        }]
        product = {
            "product_flags": [{
                "category": "pregnancy_contraindication",
                "flag_name": "임부금기",
            }]
        }

        checks = apply_product_flag_fallbacks(
            core,
            product,
            {"birth_date": "1990-01-01", "sex": "female", "pregnancy_status": "pregnant"},
            detailed_product_categories=set(),
        )

        self.assertEqual(checks[0]["status"], "hit")
        self.assertEqual(checks[0]["summary"], "임부금기 주의사항 있음")

    def test_product_info_quantitative_flag_stays_unknown_without_detail_threshold(self) -> None:
        core = [{
            "category": "dose_caution",
            "label": "용량주의",
            "status": "clear",
            "summary": "기준 이내",
            "findings": [],
        }]
        product = {"product_flags": [{"category": "dose_caution", "flag_name": "용량주의"}]}

        checks = apply_product_flag_fallbacks(
            core,
            product,
            {"birth_date": "1990-01-01", "sex": "male", "pregnancy_status": "not_applicable"},
            detailed_product_categories=set(),
        )

        self.assertEqual(checks[0]["status"], "unknown")
        self.assertIn("상세 기준", checks[0]["details"])

    def test_existing_detailed_product_category_remains_authoritative(self) -> None:
        core = [{
            "category": "dose_caution",
            "label": "용량주의",
            "status": "clear",
            "summary": "기준 이내",
            "findings": [],
        }]
        product = {"product_flags": [{"category": "dose_caution", "flag_name": "용량주의"}]}

        checks = apply_product_flag_fallbacks(
            core,
            product,
            {"birth_date": "1990-01-01", "sex": "male", "pregnancy_status": "not_applicable"},
            detailed_product_categories={"dose_caution"},
        )

        self.assertEqual(checks, core)


if __name__ == "__main__":
    unittest.main()
