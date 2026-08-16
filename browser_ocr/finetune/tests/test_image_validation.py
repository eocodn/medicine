from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import DatasetError, load_dataset


class ImageValidationTest(unittest.TestCase):
    def test_png_extension_with_non_png_bytes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "images").mkdir()
            payload = b"definitely-not-a-png"
            (root / "images/bad.png").write_bytes(payload)
            sample = {
                "id": "bad-image",
                "image": "images/bad.png",
                "image_sha256": hashlib.sha256(payload).hexdigest(),
                "text": "1정",
                "origin": "synthetic",
                "document_type": "medication_bag",
                "document_id": "doc-bad",
                "groups": {
                    "layout_family": "layout-bad",
                    "source_family": "source-bad",
                    "drug_family": "drug-bad"
                },
                "semantic_tags": ["dose"],
                "risk_tags": ["exact_numeric"],
                "privacy": {"contains_patient_data": False, "deidentified": True},
                "provenance": {"source_id": "fixture:bad", "license_id": "generated-fixture"}
            }
            (root / "samples.jsonl").write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "dataset_id": "bad-image-fixture",
                "task": "text_recognition",
                "patient_data_policy": "forbid",
                "samples_file": "samples.jsonl"
            }
            (root / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(DatasetError, "header does not match"):
                load_dataset(root / "manifest.json")


if __name__ == "__main__":
    unittest.main()
