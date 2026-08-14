from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from browser_ocr.finetune.dataset import dataset_stats, load_dataset
from browser_ocr.finetune.synthetic import GenerationError, generate_dataset


class SyntheticDatasetTest(unittest.TestCase):
    def make_canonical(self, path: Path) -> None:
        con = sqlite3.connect(path)
        con.executescript(
            """
            create table products (
                item_seq text primary key,
                product_name text not null,
                ingredient_text text,
                dosage_form text,
                permit_status text not null,
                source_dataset_key text not null
            );
            create table source_snapshots (
                dataset_key text primary key,
                source_family text not null,
                source_locator text not null,
                sha256 text not null
            );
            """
        )
        products = [
            ("100", "가나다정500밀리그램", "Acetaminophen", "정제", "정상", "mfds_permit:products"),
            ("101", "라마바캡슐", "Ibuprofen", "캡슐", "정상", "mfds_permit:products"),
            ("102", "사아자시럽", "Cetirizine HCl", "시럽", "정상", "mfds_permit:products"),
            ("103", "카타파정", "Metformin", "정제", "정상", "mfds_permit:products"),
        ]
        con.executemany("insert into products values (?,?,?,?,?,?)", products)
        con.execute(
            "insert into source_snapshots values (?,?,?,?)",
            ("mfds_permit:products", "mfds_permit_api", "https://example.invalid/mfds", "a" * 64),
        )
        con.commit()
        con.close()

    def font_path(self) -> Path:
        candidates = [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        self.fail("Noto CJK font is not installed in the fine-tune container")

    def test_generation_is_deterministic_valid_and_covers_safety_strata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            canonical = root / "canonical.sqlite"
            self.make_canonical(canonical)
            left = root / "left"
            right = root / "right"

            first = generate_dataset(canonical, left, count=40, seed=112, font_path=self.font_path())
            second = generate_dataset(canonical, right, count=40, seed=112, font_path=self.font_path())

            self.assertEqual(first["dataset_fingerprint"], second["dataset_fingerprint"])
            self.assertEqual(first["sample_count"], 40)
            self.assertEqual(first["canonical_db_sha256"], hashlib.sha256(canonical.read_bytes()).hexdigest())
            self.assertFalse((left / ".generation-state.json").exists())

            left_samples = (left / "samples.jsonl").read_text(encoding="utf-8")
            right_samples = (right / "samples.jsonl").read_text(encoding="utf-8")
            self.assertEqual(left_samples, right_samples)
            self.assertLessEqual(max(len(json.loads(line)["text"]) for line in left_samples.splitlines()), 25)

            with Image.open(left / "images" / "sample-000000.png") as image:
                image.load()
                self.assertGreater(image.width, 90)
                self.assertGreater(image.height, 35)
                self.assertNotEqual(image.getextrema()[0], image.getextrema()[1])

            dataset = load_dataset(left / "manifest.json")
            stats = dataset_stats(dataset)
            self.assertEqual(stats["sample_count"], 40)
            self.assertGreaterEqual(stats["scripts"]["korean"], 30)
            self.assertGreaterEqual(stats["scripts"]["digit"], 25)
            self.assertGreaterEqual(stats["scripts"]["latin"], 3)
            for tag in ("product", "strength", "dose", "frequency", "duration", "schedule", "clinic_hours", "phone", "date", "identifier"):
                self.assertGreater(stats["semantic_tags"].get(tag, 0), 0, tag)
            for tag in ("hard_negative", "mixed_script", "exact_numeric", "decimal", "fraction", "ambiguous_range"):
                self.assertGreater(stats["risk_tags"].get(tag, 0), 0, tag)

    def test_partial_generation_resumes_and_config_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            canonical = root / "canonical.sqlite"
            self.make_canonical(canonical)
            output = root / "dataset"
            calls = 0

            def stop_after_12(done: int, total: int) -> None:
                nonlocal calls
                calls += 1
                if done >= 12:
                    raise RuntimeError("simulated interruption")

            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                generate_dataset(
                    canonical, output, count=40, seed=7, font_path=self.font_path(),
                    progress=stop_after_12, progress_interval=4,
                )
            state = json.loads((output / ".generation-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["completed"], 12)

            report = generate_dataset(canonical, output, count=40, seed=7, font_path=self.font_path())
            self.assertEqual(report["sample_count"], 40)
            self.assertFalse((output / ".generation-state.json").exists())
            self.assertEqual(len((output / "samples.jsonl").read_text(encoding="utf-8").splitlines()), 40)

            with self.assertRaisesRegex(GenerationError, "configuration"):
                generate_dataset(canonical, output, count=41, seed=7, font_path=self.font_path())


if __name__ == "__main__":
    unittest.main()
