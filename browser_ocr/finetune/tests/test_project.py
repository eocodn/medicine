from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINETUNE = ROOT / "browser_ocr" / "finetune"


class FineTuneProjectContractTest(unittest.TestCase):
    def test_research_plan_defines_three_non_random_holdout_axes(self) -> None:
        plan = json.loads((FINETUNE / "research-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["chronicle"], 112)
        self.assertEqual(plan["baseline"]["recognizer"], "korean_PP-OCRv5_mobile_rec")
        self.assertEqual(
            {item["group_by"] for item in plan["holdout_experiments"]},
            {"layout_family", "source_family", "drug_family"},
        )
        self.assertEqual(plan["real_data_policy"]["patient_data"], "forbidden")
        self.assertEqual(plan["real_data_policy"]["phase_1_use"], "holdout-only")

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


    def test_recorded_5k_baseline_is_bound_to_synthetic_holdout(self) -> None:
        result = json.loads((FINETUNE / "results" / "synth-5k-drug-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(result["dataset_fingerprint"], "7e8ba54f066354dc6da108187e7c16ec142de8dae97b30673c75f0ea938a4f57")
        self.assertEqual(result["holdout_axis"], "drug_family")
        self.assertEqual(result["test_samples"], 500)
        self.assertFalse(result["real_data_evaluated"])
        self.assertEqual(result["best_epoch"], 2)
        self.assertGreater(result["best_test"]["acc"], result["pretrained_test"]["acc"])

    def test_recorded_source_learning_curve_uses_one_scale_stable_holdout(self) -> None:
        result = json.loads(
            (FINETUNE / "results" / "synth-v5-source-learning-curve.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["chronicle"], 112)
        self.assertEqual(result["holdout_axis"], "source_family")
        self.assertEqual(result["assignment"], "stable_family_hash_v1")
        self.assertEqual(result["held_out_families"]["test"], ["synthetic-source-04", "synthetic-source-12"])
        self.assertFalse(result["real_data_evaluated"])
        points = result["points"]
        self.assertEqual([point["crop_count"] for point in points], [5000, 20000, 50000])
        self.assertGreater(points[1]["best_test"]["acc"], points[0]["best_test"]["acc"])
        self.assertGreater(points[2]["best_test"]["acc"], points[1]["best_test"]["acc"])
        self.assertGreater(points[1]["slices"]["semantic"]["product"], points[0]["slices"]["semantic"]["product"])
        self.assertGreater(points[2]["slices"]["semantic"]["product"], points[1]["slices"]["semantic"]["product"])
        self.assertGreater(points[1]["slices"]["risk"]["mixed_script"], points[0]["slices"]["risk"]["mixed_script"])
        self.assertGreater(points[2]["slices"]["risk"]["mixed_script"], points[1]["slices"]["risk"]["mixed_script"])
        self.assertGreater(result["observed_20k_to_50k"]["relative_exact_error_reduction"], 0.5)

    def test_dataset_schema_forbids_patient_data(self) -> None:
        schema = json.loads((FINETUNE / "dataset.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["patient_data_policy"]["const"], "forbid")
        privacy = schema["$defs"]["sample"]["properties"]["privacy"]["properties"]
        self.assertFalse(privacy["contains_patient_data"]["const"])


if __name__ == "__main__":
    unittest.main()
