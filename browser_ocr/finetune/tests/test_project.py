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

    def test_upstream_training_is_disabled_until_weights_are_fully_pinned(self) -> None:
        upstream = json.loads((FINETUNE / "upstream.json").read_text(encoding="utf-8"))
        self.assertEqual(upstream["recognizer"], "korean_PP-OCRv5_mobile_rec")
        self.assertFalse(upstream["training_enabled"])
        self.assertIsNone(upstream["pretrained_model_sha256"])
        self.assertEqual(upstream["pin_status"], "pending-complete-download")

    def test_dataset_schema_forbids_patient_data(self) -> None:
        schema = json.loads((FINETUNE / "dataset.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["patient_data_policy"]["const"], "forbid")
        privacy = schema["$defs"]["sample"]["properties"]["privacy"]["properties"]
        self.assertFalse(privacy["contains_patient_data"]["const"])


if __name__ == "__main__":
    unittest.main()
