from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.intake import INTAKE_SCHEMA_VERSION, normalize_provider_envelope
from medicine_app.mobile_api import MobileApi
from medicine_app.web import create_web_app
from tests.test_app_core import make_canonical_db


class ManualOnlyIntakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        self.client = TestClient(create_web_app(self.canonical_db, self.personal_db))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_product_ui_exposes_manual_entry_only(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("약을 검색하세요", page.text)
        self.assertNotIn("ocr-scan-button", page.text)
        self.assertNotIn("ocr-image-input", page.text)
        self.assertNotIn("ocr-review-sheet", page.text)
        self.assertNotIn("browser-ocr", page.text.lower())
        self.assertNotIn("/static/ocr.js", page.text)

        app = self.client.get("/static/app.js")
        self.assertEqual(app.status_code, 200)
        self.assertNotIn("MedicineOcr", app.text)
        self.assertNotIn("ocr-preview", app.text)
        self.assertNotIn("ocr_review_token", app.text)

    def test_product_does_not_serve_ocr_runtime_or_ocr_static_modules(self) -> None:
        for path in [
            "/ocr-assets/runtime-manifest.json",
            "/static/ocr.js",
            "/static/ocr-review.js",
            "/static/browser-ocr.js",
            "/static/browser-ocr-parser.js",
        ]:
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_web_api_has_no_ocr_or_batch_ingestion_routes(self) -> None:
        person = self.client.post(
            "/api/people", json={"name": "수기", "birth_date": "1990-01-01", "sex": "male", "pregnancy_status": "not_applicable", "lactation_status": "not_applicable"}
        ).json()
        for path, payload in [
            (f"/api/people/{person['id']}/medications/ocr-preview", {}),
            (f"/api/people/{person['id']}/medications/batch-preview", {"operation_id": "x", "rows": []}),
            (f"/api/people/{person['id']}/medications/batch", {"request_id": "x", "rows": []}),
        ]:
            self.assertEqual(self.client.post(path, json=payload).status_code, 404, path)

        preview = self.client.post(
            f"/api/people/{person['id']}/medications/preview",
            json={"ocr_envelope": {"version": 1}},
        )
        self.assertEqual(preview.status_code, 422)
        create = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={"manual_name": "수기약", "ocr_review_token": "x"},
        )
        self.assertEqual(create.status_code, 422)

    def test_android_bridge_has_no_ocr_or_batch_ingestion_routes(self) -> None:
        api = MobileApi(self.canonical_db, self.personal_db)
        created = json.loads(api.request(
            "POST", "/api/people", json.dumps({"name": "수기", "birth_date": "1990-01-01", "sex": "male", "pregnancy_status": "not_applicable", "lactation_status": "not_applicable"})
        ))
        person_id = created["body"]["id"]
        for path in [
            f"/api/people/{person_id}/medications/ocr-preview",
            f"/api/people/{person_id}/medications/batch-preview",
            f"/api/people/{person_id}/medications/batch",
        ]:
            response = json.loads(api.request("POST", path, "{}"))
            self.assertEqual(response["status"], 404, path)

        response = json.loads(api.request(
            "POST",
            f"/api/people/{person_id}/medications",
            json.dumps({"manual_name": "수기약", "ocr_origin": True}),
        ))
        self.assertEqual(response["status"], 400)


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
