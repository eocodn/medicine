from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.kids_sources import KIDS_DOWNLOADS, sync_kids_xlsx_sources
from medicine_canonical.xlsx import XLSX_DATASETS


HEADERS = {
    "combination_contraindication": ["연번", "유효성분 '1'", "유효성분 '2'"],
    "age_contraindication": ["연번", "성분명", "연령기준"],
    "pregnancy_contraindication": ["연번", "성분명", "임부금기(등급)"],
    "dose_caution": ["연번", "성분명(국문)", "성분명(영문)", "1일 최대용량"],
    "duration_caution": ["연번", "성분명(국문)", "성분명(영문)", "최대 투여기간"],
    "elderly_caution": ["연번", "성분명(국문)", "성분명(영문)"],
    "therapeutic_duplication_caution": ["연번", "효능군", "성분명(국문)", "성분명(영문)"],
    "lactation_caution": ["연번", "성분명(국문)", "성분명(영문)"],
}


def workbook_bytes(category: str, title: str = "테스트 (2026.08.16. 공고 기준)") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([title])
    sheet.append(HEADERS[category])
    sheet.append([1] + ["x"] * (len(HEADERS[category]) - 1))
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class KidsSourceSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_download_spec_matches_exact_canonical_xlsx_source_set(self) -> None:
        self.assertEqual(
            {spec.filename: spec.category for spec in KIDS_DOWNLOADS},
            XLSX_DATASETS,
        )

    def test_sync_downloads_current_centered_attachment_and_validates_all_eight_xlsx(self) -> None:
        attachment_by_page: dict[str, str] = {}
        payload_by_id: dict[str, bytes] = {}
        for index, spec in enumerate(KIDS_DOWNLOADS, start=1):
            attachment = f"FILE_{index:015d}"
            attachment_by_page[spec.page_url] = attachment
            payload_by_id[attachment] = workbook_bytes(spec.category)

        def fetch_page(url: str) -> str:
            attachment = attachment_by_page[url]
            return f"""
                <script>function fn_egov_downFile(atchFileId, fileSn) {{}}</script>
                <div align="center"><a href="javascript:fn_egov_downFile('{attachment}','0')">current</a></div>
                <div><a href="javascript:fn_egov_downFile('FILE_999999999999999','0')">old</a></div>
            """

        def fetch_attachment(url: str) -> bytes:
            attachment = url.split("atchFileId=", 1)[1].split("&", 1)[0]
            return payload_by_id[attachment]

        output = self.root / "kids"
        progress: list[str] = []
        result = sync_kids_xlsx_sources(
            output,
            fetch_page=fetch_page,
            fetch_attachment=fetch_attachment,
            progress=progress.append,
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(len(result["sources"]), 8)
        self.assertEqual(sorted(p.name for p in output.glob("*.xlsx")), sorted(s.filename for s in KIDS_DOWNLOADS))
        for source in result["sources"]:
            self.assertRegex(source["attachment_id"], r"^FILE_\d+$")
            self.assertEqual(source["effective_date"], "2026-08-16")
            self.assertTrue(source["sha256"])
            self.assertGreater(source["size_bytes"], 0)

        manifest = json.loads((output / ".kids-source-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(len(manifest["sources"]), 8)
        self.assertIn("[1/8] combination.xlsx: fetch page", progress)
        self.assertTrue(any("combination.xlsx: verified" in message for message in progress))
        self.assertTrue(any("[8/8] lactation.xlsx: verified" in message for message in progress))

    def test_same_verified_snapshot_is_idempotent_but_corruption_forces_replacement(self) -> None:
        attachment_by_page: dict[str, str] = {}
        payload_by_id: dict[str, bytes] = {}
        for index, spec in enumerate(KIDS_DOWNLOADS, start=1):
            attachment = f"FILE_{index:015d}"
            attachment_by_page[spec.page_url] = attachment
            payload_by_id[attachment] = workbook_bytes(spec.category)

        def fetch_page(url: str) -> str:
            attachment = attachment_by_page[url]
            return f"<div align='center'><a href=\"javascript:fn_egov_downFile('{attachment}','0')\">current</a></div>"

        def fetch_attachment(url: str) -> bytes:
            attachment = url.split("atchFileId=", 1)[1].split("&", 1)[0]
            return payload_by_id[attachment]

        output = self.root / "kids"
        first = sync_kids_xlsx_sources(output, fetch_page=fetch_page, fetch_attachment=fetch_attachment)
        second = sync_kids_xlsx_sources(output, fetch_page=fetch_page, fetch_attachment=fetch_attachment)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(second["status"], "unchanged")

        (output / "age.xlsx").write_bytes(b"corrupted")
        repaired = sync_kids_xlsx_sources(output, fetch_page=fetch_page, fetch_attachment=fetch_attachment)
        self.assertEqual(repaired["status"], "updated")
        self.assertNotEqual((output / "age.xlsx").read_bytes(), b"corrupted")

    def test_sync_failure_keeps_existing_directory_untouched(self) -> None:
        output = self.root / "kids"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("old", encoding="utf-8")

        def fetch_page(url: str) -> str:
            if url == KIDS_DOWNLOADS[0].page_url:
                return "<html>download link missing</html>"
            raise AssertionError("sync should fail on the first invalid page")

        with self.assertRaisesRegex(ValueError, "current KIDS XLSX attachment"):
            sync_kids_xlsx_sources(output, fetch_page=fetch_page, fetch_attachment=lambda _: b"")

        self.assertEqual(marker.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(output.glob("*.xlsx")), [])

    def test_sync_rejects_download_that_is_not_a_valid_category_xlsx(self) -> None:
        spec = KIDS_DOWNLOADS[0]
        page = "<div align='center'><a href=\"javascript:fn_egov_downFile('FILE_000000000000001','0')\">x</a></div>"
        with self.assertRaisesRegex(ValueError, "XLSX|header"):
            sync_kids_xlsx_sources(
                self.root / "kids",
                fetch_page=lambda url: page if url == spec.page_url else page,
                fetch_attachment=lambda _: b"<html>not an xlsx</html>",
            )

    def test_sync_rejects_xlsx_without_effective_date(self) -> None:
        page = "<div align='center'><a href=\"javascript:fn_egov_downFile('FILE_000000000000001','0')\">x</a></div>"
        with self.assertRaisesRegex(ValueError, "effective date"):
            sync_kids_xlsx_sources(
                self.root / "kids",
                fetch_page=lambda _: page,
                fetch_attachment=lambda _: workbook_bytes(KIDS_DOWNLOADS[0].category, title="날짜 없는 테스트"),
            )

    def test_cli_exposes_kids_sync_json_command(self) -> None:
        expected = {"status": "updated", "sources": []}
        with mock.patch("medicine_canonical.cli.sync_kids_xlsx_sources", return_value=expected) as sync:
            code = canonical_main(["kids-sync", "--output-dir", str(self.root / "kids"), "--json"])
        self.assertEqual(code, 0)
        sync.assert_called_once()
        self.assertEqual(sync.call_args.args, (self.root / "kids",))
        self.assertTrue(callable(sync.call_args.kwargs["progress"]))


if __name__ == "__main__":
    unittest.main()