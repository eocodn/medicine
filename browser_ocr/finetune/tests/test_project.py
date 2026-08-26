from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINETUNE = ROOT / "browser_ocr" / "finetune"


class FineTuneProjectContractTest(unittest.TestCase):
    def test_upstream_model_and_source_are_cryptographically_pinned(self) -> None:
        upstream = json.loads((FINETUNE / "upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(upstream["recognizer"], "korean_PP-OCRv5_mobile_rec")
        self.assertEqual(upstream["paddleocr"]["tag"], "v3.7.0")
        self.assertEqual(upstream["paddleocr"]["commit"], "b03f46425e8ff4442b268ce449e3eef758146cd4")
        self.assertRegex(upstream["paddleocr"]["config_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(upstream["paddleocr"]["dictionary_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(upstream["pretrained_model_bytes"], 110996478)
        self.assertEqual(upstream["pretrained_model_sha256"], "8975dede5e0c2f47e0a7712b3d79ffdc766972f872fd0441ebcccd9d77cd52a3")
        self.assertEqual(upstream["pin_status"], "training-smoke-verified")
        self.assertTrue(upstream["training_enabled"])
        runtime = upstream["training_runtime"]
        self.assertEqual(runtime["paddlepaddle"], "3.2.0")
        self.assertEqual(runtime["cuda_runtime"], "12.6")
        self.assertEqual(runtime["cudnn"], "9.5.1.17")
        self.assertEqual(runtime["verified_gpu"]["name"], "NVIDIA GeForce RTX 4080")
        self.assertEqual(runtime["verified_gpu"]["vram_mib"], 16376)

    def test_historical_drug_exposure_metadata_remains_for_v6_split_reproducibility(self) -> None:
        exposure = json.loads(
            (FINETUNE / "results" / "selected-100k-training-drug-exposure.json").read_text(encoding="utf-8")
        )
        self.assertEqual(exposure["id"], "selected-recognizer-training-exposure-v1")
        self.assertRegex(exposure["checkpoint_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(exposure["source_dataset_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(exposure["source_train_split_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(exposure["source_train_sample_count"], 0)
        self.assertGreater(exposure["family_count"], 0)

    def test_dataset_schema_forbids_patient_data(self) -> None:
        schema = json.loads((FINETUNE / "dataset.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["patient_data_policy"]["const"], "forbid")
        privacy = schema["$defs"]["sample"]["properties"]["privacy"]["properties"]
        self.assertFalse(privacy["contains_patient_data"]["const"])


if __name__ == "__main__":
    unittest.main()
