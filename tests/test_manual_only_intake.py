from __future__ import annotations

import unittest
from pathlib import Path

from medicine_app.intake import INTAKE_SCHEMA_VERSION, normalize_provider_envelope


class ManualOnlyIntakeTest(unittest.TestCase):
    def test_shared_ui_routes_parser_output_directly_to_product_search(self) -> None:
        static = Path("medicine_app/static")
        page = (static / "index.html").read_text(encoding="utf-8")
        app = (static / "app.js").read_text(encoding="utf-8")
        intake = (static / "ocr-intake.js").read_text(encoding="utf-8")

        self.assertIn("약을 검색하세요", page)
        self.assertNotIn("ocr-scan-button", page)
        self.assertIn("ocr-image-input", page)
        self.assertNotIn("browser-ocr", page.lower())
        self.assertNotIn("/static/ocr.js", page)
        self.assertIn("/static/ocr-intake.js", page)
        self.assertIn("사진은 서버로 전송되지 않고 이 기기에서만 인식", page)
        self.assertIn("medicine:parser-result", app)
        self.assertIn("runDrugSearch", app)
        self.assertNotIn("ocr-preview", app)
        self.assertNotIn("ocr_review_token", app)
        self.assertIn('new Worker("/ocr-assets/direct/ocr-worker.js")', intake)
        self.assertIn("medicine:parser-result", intake)
        self.assertNotIn("/api/ocr", intake)


class FutureProviderContractTest(unittest.TestCase):
    def test_future_provider_contract_accepts_multi_row_structured_drafts_without_identity_claims(self) -> None:
        normalized = normalize_provider_envelope({
            "schema_version": INTAKE_SCHEMA_VERSION,
            "provider_id": "future-finetuned-ocr",
            "rows": [{
                "row_id": "row-1",
                "product_query": "타이레놀정",
                "draft": {
                    "dose_amount": 1,
                    "dose_unit": "정",
                    "frequency_per_day": 3,
                    "prescription_days": 5,
                    "schedule_times": ["08:00", "13:00", "19:00"],
                },
                "uncertainty_codes": ["LOW_CONFIDENCE_DOSE"],
            }],
        })
        row = normalized["rows"][0]
        self.assertEqual(row["product_query"], "타이레놀정")
        self.assertNotIn("product_ref", row)
        self.assertEqual(row["draft"]["frequency_per_day"], 3)
        self.assertEqual(row["draft"]["schedule_times"], ["08:00", "13:00", "19:00"])
        self.assertEqual(row["uncertainty_codes"], ["LOW_CONFIDENCE_DOSE"])

    def test_future_provider_contract_rejects_raw_artifacts_identity_claims_and_unknown_fields(self) -> None:
        base = {
            "schema_version": INTAKE_SCHEMA_VERSION,
            "provider_id": "future-finetuned-ocr",
            "rows": [{"row_id": "row-1", "product_query": "테스트정", "draft": {}}],
        }
        invalid = [
            {**base, "raw_text": "patient data"},
            {**base, "unexpected": True},
            {**base, "rows": [{**base["rows"][0], "image_uri": "file:///secret.jpg"}]},
            {**base, "rows": [{**base["rows"][0], "product_ref": "MFDS-123"}]},
        ]
        for value in invalid:
            with self.assertRaises(ValueError):
                normalize_provider_envelope(value)


if __name__ == "__main__":
    unittest.main()
