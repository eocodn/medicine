from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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



if __name__ == "__main__":
    unittest.main()
