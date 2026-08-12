from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.coverage import resolve_safety_mapping
from medicine_dur.db import build_database, database_stats


BRIDGE_HEADER = [
    "한글상품명", "업체명", "약품규격", "제품총수량", "제형구분", "포장형태",
    "품목기준코드", "품목허가일자", "전문일반구분", "대표코드", "표준코드",
    "제품코드(개정후)", "일반명코드(성분명코드)", "비고", "취소일자",
    "양도양수적용(공고)일자", "양도양수종료일자", "일련번호생략여부",
    "일련번호생략사유", "국제표준코드(ATC코드)", "특수관리약품구분", "의약품판독장비구분",
]


def _bridge_row(item_seq: str, product_code: str, name: str = "테스트정") -> list[str]:
    values = {key: "" for key in BRIDGE_HEADER}
    values.update({
        "한글상품명": name,
        "품목기준코드": item_seq,
        "제품코드(개정후)": product_code,
        "표준코드": "8800000000000",
    })
    return [values[key] for key in BRIDGE_HEADER]


class ProductCodeBridgeImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = self.root / "raw"
        self.kids = self.root / "kids"
        self.raw.mkdir()
        self.kids.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_imports_distinct_hira_item_to_product_code_relations_with_provenance(self) -> None:
        path = self.raw / "hira_product_code_bridge.csv"
        with path.open("w", encoding="cp949", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(BRIDGE_HEADER)
            writer.writerow(_bridge_row("MFDS-1 ", "P-1 "))
            writer.writerow(_bridge_row("MFDS-1", "P-1"))
            writer.writerow(_bridge_row("MFDS-2", "P-2"))
            writer.writerow(_bridge_row("MFDS-3", ""))

        db = self.root / "dur.sqlite"
        result = build_database(db, self.raw, self.kids, progress=False)

        self.assertEqual(result["product_bridge_rows"], 2)
        self.assertEqual(database_stats(db)["product_bridge_rows"], 2)
        con = sqlite3.connect(db)
        try:
            self.assertEqual(
                con.execute(
                    "SELECT item_seq,product_code FROM product_code_bridge ORDER BY item_seq,product_code"
                ).fetchall(),
                [("MFDS-1", "P-1"), ("MFDS-2", "P-2")],
            )
            source = con.execute(
                "SELECT dataset_key,source_kind,row_count,metadata_json FROM source_files"
            ).fetchone()
            self.assertEqual(source[:3], ("product_bridge:hira_standard_code", "product_bridge", 2))
            metadata = json.loads(source[3])
            self.assertEqual(metadata["source_rows"], 4)
            self.assertEqual(metadata["encoding"], "cp949")
        finally:
            con.close()


def _mapping_db(*, bridge_rows: list[tuple[str, str]], product_rows: list[tuple[str, str]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE product_catalog(
            product_code TEXT PRIMARY KEY, product_name TEXT NOT NULL,
            ingredient_code TEXT, ingredient_name TEXT
        );
        CREATE TABLE ingredient_dur(ingredient_name TEXT, paired_ingredient_name TEXT);
        CREATE TABLE product_code_bridge(
            dataset_key TEXT NOT NULL, item_seq TEXT NOT NULL, product_code TEXT NOT NULL,
            product_name TEXT, standard_code TEXT, PRIMARY KEY(item_seq,product_code)
        );
        INSERT INTO ingredient_dur VALUES('caffeine', NULL);
        """
    )
    con.executemany(
        "INSERT INTO product_catalog VALUES(?,?,?,?)",
        [(code, name, "ING-1", "caffeine") for code, name in product_rows],
    )
    con.executemany(
        "INSERT INTO product_code_bridge VALUES('product_bridge:hira_standard_code',?,?,NULL,NULL)",
        bridge_rows,
    )
    return con


class ProductCodeBridgeResolutionTest(unittest.TestCase):
    def test_single_exact_hira_bridge_replaces_name_heuristic(self) -> None:
        con = _mapping_db(bridge_rows=[("MFDS-1", "P-1")], product_rows=[("P-1", "다른표기")])
        mapping = resolve_safety_mapping(
            con,
            catalog_item_seq="MFDS-1",
            edi_value=None,
            catalog_product_name="테스트정",
            catalog_ingredient="Caffeine",
        )
        self.assertEqual(mapping["product_code"], "P-1")
        self.assertEqual(mapping["product_mapping_method"], "item_seq_hira_exact")
        self.assertEqual(mapping["product_status"], "matched")
        self.assertEqual(mapping["product_identity_method"], "item_seq_hira_exact")

    def test_current_edi_remains_authoritative_over_historical_hira_bridge(self) -> None:
        con = _mapping_db(
            bridge_rows=[("MFDS-1", "P-OLD")],
            product_rows=[("P-OLD", "과거제품"), ("P-CURRENT", "현재제품")],
        )
        mapping = resolve_safety_mapping(
            con,
            catalog_item_seq="MFDS-1",
            edi_value="P-CURRENT",
            catalog_product_name="현재제품",
            catalog_ingredient="Caffeine",
        )
        self.assertEqual(mapping["product_code"], "P-CURRENT")
        self.assertEqual(mapping["product_mapping_method"], "edi_exact")

    def test_unmatched_current_edi_can_use_single_exact_hira_bridge(self) -> None:
        con = _mapping_db(
            bridge_rows=[("MFDS-1", "P-DUR")],
            product_rows=[("P-DUR", "테스트정_(1정/1정)")],
        )
        mapping = resolve_safety_mapping(
            con,
            catalog_item_seq="MFDS-1",
            edi_value="P-CURRENT-WITHOUT-DUR-ROW",
            catalog_product_name="테스트정",
            catalog_ingredient="Caffeine",
        )
        self.assertEqual(mapping["edi_codes"], ["P-CURRENT-WITHOUT-DUR-ROW"])
        self.assertEqual(mapping["bridge_product_codes"], ["P-DUR"])
        self.assertEqual(mapping["product_code"], "P-DUR")
        self.assertEqual(mapping["product_mapping_method"], "item_seq_hira_exact")

    def test_multiple_hira_codes_fail_closed_instead_of_using_name_heuristic(self) -> None:
        con = _mapping_db(
            bridge_rows=[("MFDS-1", "P-1"), ("MFDS-1", "P-2")],
            product_rows=[("P-1", "테스트정_(1정/1정)"), ("P-2", "다른제품")],
        )
        mapping = resolve_safety_mapping(
            con,
            catalog_item_seq="MFDS-1",
            edi_value=None,
            catalog_product_name="테스트정",
            catalog_ingredient="Caffeine",
        )
        self.assertIsNone(mapping["product_code"])
        self.assertEqual(mapping["product_mapping_method"], "item_seq_hira_ambiguous")
        self.assertEqual(mapping["product_status"], "ambiguous")
        self.assertEqual(mapping["bridge_product_codes"], ["P-1", "P-2"])

    def test_bridge_code_without_detailed_dur_row_blocks_name_fallback(self) -> None:
        con = _mapping_db(
            bridge_rows=[("MFDS-1", "P-NO-RULE")],
            product_rows=[("P-LOOKALIKE", "테스트정_(1정/1정)")],
        )
        mapping = resolve_safety_mapping(
            con,
            catalog_item_seq="MFDS-1",
            edi_value=None,
            catalog_product_name="테스트정",
            catalog_ingredient="Caffeine",
        )
        self.assertIsNone(mapping["product_code"])
        self.assertEqual(mapping["product_mapping_method"], "item_seq_hira_no_detail")
        self.assertEqual(mapping["product_status"], "not_matched")
        self.assertEqual(mapping["bridge_product_codes"], ["P-NO-RULE"])


if __name__ == "__main__":
    unittest.main()
