from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.source_bundle import extract_kids_bundle, pack_kids_bundle
from medicine_canonical.xlsx import XLSX_DATASETS


class KidsSourceBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kids = self.root / "kids"
        self.kids.mkdir()
        for index, filename in enumerate(XLSX_DATASETS, start=1):
            (self.kids / filename).write_bytes(f"xlsx-{index}".encode())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pack_and_extract_requires_exact_expected_source_set(self) -> None:
        bundle = self.root / "kids.zip"
        packed = pack_kids_bundle(self.kids, bundle)
        output = self.root / "extracted"
        extracted = extract_kids_bundle(bundle, output)

        self.assertEqual(packed["files"], sorted(XLSX_DATASETS))
        self.assertEqual(extracted["files"], sorted(XLSX_DATASETS))
        for filename in XLSX_DATASETS:
            self.assertEqual((output / filename).read_bytes(), (self.kids / filename).read_bytes())

    def test_pack_rejects_missing_source(self) -> None:
        (self.kids / "age.xlsx").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "age.xlsx"):
            pack_kids_bundle(self.kids, self.root / "kids.zip")

    def test_cli_packs_and_extracts_bundle(self) -> None:
        bundle = self.root / "cli-kids.zip"
        self.assertEqual(
            canonical_main(["kids-bundle", "--kids-dir", str(self.kids), "--output", str(bundle), "--json"]),
            0,
        )
        output = self.root / "cli-output"
        self.assertEqual(
            canonical_main(["kids-extract", "--bundle", str(bundle), "--output-dir", str(output), "--json"]),
            0,
        )
        self.assertEqual(sorted(p.name for p in output.iterdir()), sorted(XLSX_DATASETS))

    def test_extract_rejects_extra_or_nested_members(self) -> None:
        bundle = self.root / "bad.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            for filename in XLSX_DATASETS:
                archive.writestr(filename, b"ok")
            archive.writestr("nested/extra.xlsx", b"bad")
        with self.assertRaisesRegex(ValueError, "exactly"):
            extract_kids_bundle(bundle, self.root / "out")


if __name__ == "__main__":
    unittest.main()
