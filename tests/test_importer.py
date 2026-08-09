import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from openpyxl import Workbook

from medicine_dur.db import build_database, database_stats, search_records


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.raw_dir = self.root / "raw"
        self.kids_dir = self.root / "kids"
        self.raw_dir.mkdir()
        self.kids_dir.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_cp949_csv(self, filename, header, rows):
        path = self.raw_dir / filename
        with path.open("w", encoding="cp949", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def _write_kids_combination_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.title = "병용금기"
        ws.append(["병용금기 의약품 (test)", None, None, None, None])
        ws.append(["연번", "유효성분 '1'", "유효성분 '2'", "비고", "상세정보"])
        ws.append([1, "acemetacin", "diflunisal", None, "중증의 위장관계 이상반응"])
        path = self.kids_dir / "combination.xlsx"
        wb.save(path)
        return path

    def test_builds_searchable_database_from_product_and_ingredient_sources(self):
        self._write_cp949_csv(
            "drug_combination_contraindication.csv",
            [
                "성분명A", "성분코드A", "제품코드A", "제품명A", "업소명A", "구분A",
                "성분명B", "성분코드B", "제품코드B", "제품명B", "업소명B", "구분B",
                "고시번호", "고시적용일", "금기사유", "비고",
            ],
            [[
                "acemetacin", "A001", "P001", "아세메타신정", "제약A", "급여",
                "diflunisal", "B001", "P002", "디플루니살정", "제약B", "급여",
                "고시-1", "2026-07-21", "중증의 위장관계 이상반응", "",
            ]],
        )
        self._write_kids_combination_xlsx()
        db_path = self.root / "dur.sqlite"

        result = build_database(db_path, self.raw_dir, self.kids_dir, progress=False)

        self.assertEqual(result["product_rows"], 1)
        self.assertEqual(result["ingredient_rows"], 1)
        self.assertEqual(result["source_files"], 2)
        self.assertTrue(db_path.exists())

        stats = database_stats(db_path)
        self.assertEqual(stats["product_rows"], 1)
        self.assertEqual(stats["ingredient_rows"], 1)
        self.assertEqual(stats["catalog_products"], 2)

        with closing(sqlite3.connect(db_path)) as conn:
            catalog_codes = {row[0] for row in conn.execute("SELECT product_code FROM product_catalog")}
        self.assertEqual(catalog_codes, {"P001", "P002"})

        matches = search_records(db_path, "diflunisal", limit=10)
        self.assertEqual(len(matches), 2)
        self.assertEqual({m["source_kind"] for m in matches}, {"product", "ingredient"})
        self.assertTrue(all(m["category"] == "combination_contraindication" for m in matches))

    def test_rebuild_replaces_database_instead_of_duplicating_rows(self):
        self._write_cp949_csv(
            "duration_caution.csv",
            ["제품코드", "제품명", "성분코드", "성분명", "최대투여기간일수", "공고일자", "공고번호", "급여구분"],
            [["P100", "졸피뎀정", "I100", "zolpidem", "28", "2026-06-09", "공고-1", "급여"]],
        )
        db_path = self.root / "dur.sqlite"

        first = build_database(db_path, self.raw_dir, self.kids_dir, progress=False)
        second = build_database(db_path, self.raw_dir, self.kids_dir, progress=False)

        self.assertEqual(first["product_rows"], 1)
        self.assertEqual(second["product_rows"], 1)
        with closing(sqlite3.connect(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM product_dur").fetchone()[0]
        self.assertEqual(count, 1)

    def test_normalizes_codes_and_repairs_therapeutic_duplication_columns(self):
        self._write_cp949_csv(
            "therapeutic_duplication_caution.csv",
            [
                "효능군", "그룹구분", "일반명코드", "성분코드", "성분명", "제품코드",
                "제품명", "업체명", "급여구분", "공고번호", "공고일자",
            ],
            [[
                "최면진정제", "Group 2", "112B0008", "zolpidem", "2505 1ATB", "66810 180",
                "산도스졸피뎀정10mg", "제약A", "급여", "20170079", "2017-02-16",
            ]],
        )
        db_path = self.root / "dur.sqlite"

        build_database(db_path, self.raw_dir, self.kids_dir, progress=False)

        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT ingredient_name, ingredient_code, product_code, rule_value FROM product_dur"
            ).fetchone()
        self.assertEqual(row, ("zolpidem", "250501ATB", "668100180", "최면진정제"))


if __name__ == "__main__":
    unittest.main()
