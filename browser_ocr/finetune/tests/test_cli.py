from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class FineTuneCliTest(unittest.TestCase):
    def make_dataset(self, root: Path) -> Path:
        (root / "images").mkdir(parents=True)
        samples = []
        for index, (text, layout, drug) in enumerate([
            ("타이레놀정 1정", "layout-a", "drug-a"),
            ("Tylenol 500mg", "layout-b", "drug-b"),
            ("1일 3회", "layout-c", "drug-c"),
            ("식후 30분", "layout-d", "drug-d"),
        ], start=1):
            image_name = f"images/sample-{index}.png"
            payload = b"\x89PNG\r\n\x1a\n" + f"fixture-{index}".encode()
            (root / image_name).write_bytes(payload)
            samples.append({
                "id": f"sample-{index}",
                "image": image_name,
                "image_sha256": hashlib.sha256(payload).hexdigest(),
                "text": text,
                "origin": "synthetic",
                "document_type": "prescription",
                "document_id": f"doc-{index}",
                "groups": {
                    "layout_family": layout,
                    "source_family": f"source-{index}",
                    "drug_family": drug,
                },
                "semantic_tags": ["product"] if index <= 2 else ["frequency"],
                "risk_tags": ["mixed_script"] if index == 2 else ["exact_numeric"],
                "privacy": {"contains_patient_data": False, "deidentified": True},
                "provenance": {"source_id": f"fixture:{index}", "license_id": "generated-fixture"},
            })
        (root / "samples.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in samples),
            encoding="utf-8",
        )
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": "cli-fixture",
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
        }, indent=2) + "\n", encoding="utf-8")
        return manifest

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "browser_ocr.finetune.cli", *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_validate_split_and_export_have_machine_readable_json_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = self.make_dataset(root)
            validated = self.run_cli("validate", "--manifest", str(manifest), "--json")
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(json.loads(validated.stdout)["status"], "ok")

            split_path = root / "split.json"
            split = self.run_cli(
                "split", "--manifest", str(manifest), "--group-by", "layout_family",
                "--seed", "112", "--output", str(split_path), "--json",
            )
            self.assertEqual(split.returncode, 0, split.stderr)
            split_body = json.loads(split.stdout)
            self.assertEqual(split_body["group_by"], "layout_family")
            self.assertEqual(set(split_body["splits"]), {"train", "val", "test"})
            self.assertTrue(split_path.is_file())

            export_dir = root / "paddle"
            exported = self.run_cli(
                "export-paddle", "--manifest", str(manifest), "--split", str(split_path),
                "--output-dir", str(export_dir), "--json",
            )
            self.assertEqual(exported.returncode, 0, exported.stderr)
            export_body = json.loads(exported.stdout)
            self.assertEqual(export_body["sample_count"], 4)
            self.assertTrue((export_dir / "train.txt").is_file())
            self.assertIn("[ocr-finetune] export 4/4", exported.stderr)

    def test_json_errors_are_machine_readable(self) -> None:
        result = self.run_cli("validate", "--manifest", "/does/not/exist.json", "--json")
        self.assertEqual(result.returncode, 2)
        body = json.loads(result.stderr)
        self.assertEqual(body["status"], "error")
        self.assertIn("does not exist", body["error"])


if __name__ == "__main__":
    unittest.main()
