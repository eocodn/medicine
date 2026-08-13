from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medicine_app.ocr import OCRReviewStore, OCRValidationError, inspect_envelope, normalize_hint_envelope
from medicine_app.web import create_web_app
from tests.test_app_core import make_catalog_db, make_dur_db


class OcrContractTest(unittest.TestCase):
    def test_review_tokens_expire_with_injected_monotonic_clock_and_cleanup_is_bounded(self) -> None:
        clock = [100.0]
        store = OCRReviewStore(clock=lambda: clock[0], ttl_seconds=5.0, max_tokens=2)
        first = store.issue("person", "product", "draft-1")
        second = store.issue("person", "product", "draft-2")
        third = store.issue("person", "product", "draft-3")
        self.assertEqual(store.size, 2)
        self.assertFalse(store.verify(first, "person", "product", "draft-1"))
        self.assertTrue(store.verify(third, "person", "product", "draft-3"))
        clock[0] = 106.0
        self.assertFalse(store.verify(second, "person", "product", "draft-2"))
        self.assertFalse(store.verify(third, "person", "product", "draft-3"))
        self.assertEqual(store.size, 0)

    def test_versioned_envelope_normalizes_structured_hints(self) -> None:
        result = normalize_hint_envelope(
            {
                "version": 1,
                "operation_id": "op-1",
                "hints": {
                    "product_name": "테스트 약",
                    "dose": "1정",
                    "frequency": "하루 2회",
                    "days": "5일",
                    "times": ["8:00", "20:00"],
                },
            }
        )
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["draft"]["dose_amount"], "1")
        self.assertEqual(result["draft"]["frequency_per_day"], 2)
        self.assertEqual(result["draft"]["prescription_days"], 5)
        self.assertEqual(result["draft"]["schedule_times"], ["08:00", "20:00"])
        self.assertEqual(result["issues"], [])

    def test_raw_artifacts_are_rejected(self) -> None:
        with self.assertRaises(OCRValidationError) as caught:
            normalize_hint_envelope(
                {"version": 1, "operation_id": "op-1", "hints": {"raw_text": "SECRET"}}
            )
        self.assertIn("raw_text", str(caught.exception))

    def test_inspect_is_no_write_and_exposes_issues(self) -> None:
        payload = {"version": 1, "operation_id": "op-1", "hints": {"product_ref": "MFDS-A", "frequency": "many"}}
        result = inspect_envelope(payload)
        self.assertTrue(result["issues"])
        self.assertIn("frequency", result["issues"][0]["field"])

    def test_ocr_preview_and_create_do_not_persist_raw_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dur_db, catalog_db, personal_db = root / "dur.sqlite", root / "catalog.sqlite", root / "personal.sqlite"
            make_dur_db(dur_db)
            make_catalog_db(catalog_db)
            from fastapi.testclient import TestClient

            client = TestClient(create_web_app(dur_db, personal_db))
            person = client.post(
                "/api/people",
                json={"name": "OCR", "birth_date": "1990-01-01"},
            ).json()
            envelope = {
                "version": 1,
                "operation_id": "op-1",
                "hints": {
                    "product_name": "테스트 약",
                    "product_ref": "MFDS-A",
                    "dose": "1정",
                    "frequency": "하루 1회",
                    "days": "5일",
                    "times": ["08:00"],
                },
            }
            preview = client.post(f"/api/people/{person['id']}/medications/ocr-preview", json=envelope)
            self.assertEqual(preview.status_code, 200)
            body = preview.json()
            self.assertTrue(body["ocr_review_token"])
            created = client.post(
                f"/api/people/{person['id']}/medications",
                json={
                    "product_ref": "MFDS-A",
                    "dose_amount": 1,
                    "dose_unit": "정",
                    "frequency_per_day": 1,
                    "prescription_days": 5,
                    "schedule_times": ["08:00"],
                    "request_id": "ocr-create-1",
                    "ocr_review_token": body["ocr_review_token"],
                },
            )
            self.assertEqual(created.status_code, 201)
            with sqlite3.connect(personal_db) as con:
                tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                contents = " ".join(
                    str(value) for table in tables for row in con.execute(f'SELECT * FROM "{table}"') for value in row
                )
            self.assertNotIn("SECRET", contents)

    def test_changed_draft_invalidates_token_and_retries_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dur_db, catalog_db, personal_db = root / "dur.sqlite", root / "catalog.sqlite", root / "personal.sqlite"
            make_dur_db(dur_db)
            make_catalog_db(catalog_db)
            from fastapi.testclient import TestClient

            client = TestClient(create_web_app(dur_db, personal_db))
            person = client.post("/api/people", json={"name": "OCR", "birth_date": "1990-01-01"}).json()
            envelope = {
                "version": 1, "operation_id": "op-2",
                "hints": {"product_ref": "MFDS-A", "dose": "1정", "frequency": "1회", "days": "5일", "times": ["08:00"]},
            }
            token = client.post(f"/api/people/{person['id']}/medications/ocr-preview", json=envelope).json()["ocr_review_token"]
            changed = client.post(
                f"/api/people/{person['id']}/medications",
                json={"product_ref": "MFDS-A", "dose_amount": 2, "dose_unit": "정", "frequency_per_day": 1,
                      "prescription_days": 5, "schedule_times": ["08:00"], "request_id": "ocr-2", "ocr_review_token": token},
            )
            self.assertEqual(changed.status_code, 400)
            first = client.post(
                f"/api/people/{person['id']}/medications",
                json={"product_ref": "MFDS-A", "dose_amount": 1, "dose_unit": "정", "frequency_per_day": 1,
                      "prescription_days": 5, "schedule_times": ["08:00"], "request_id": "ocr-3", "ocr_review_token": token},
            )
            self.assertEqual(first.status_code, 400)
            fresh = client.post(f"/api/people/{person['id']}/medications/ocr-preview", json=envelope).json()["ocr_review_token"]
            created = client.post(
                f"/api/people/{person['id']}/medications",
                json={"product_ref": "MFDS-A", "dose_amount": 1, "dose_unit": "정", "frequency_per_day": 1,
                      "prescription_days": 5, "schedule_times": ["08:00"], "request_id": "ocr-3", "ocr_review_token": fresh},
            )
            self.assertEqual(created.status_code, 201)

    def test_ocr_endpoint_and_medication_models_reject_raw_artifact_extras(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dur_db, catalog_db, personal_db = root / "dur.sqlite", root / "catalog.sqlite", root / "personal.sqlite"
            make_dur_db(dur_db)
            make_catalog_db(catalog_db)
            from fastapi.testclient import TestClient

            client = TestClient(create_web_app(dur_db, personal_db))
            person = client.post("/api/people", json={"name": "OCR", "birth_date": "1990-01-01"}).json()
            envelope = {
                "version": 1, "operation_id": "strict-1",
                "hints": {"product_ref": "MFDS-A", "dose": "1정", "frequency": "1회", "days": "5일", "times": ["08:00"]},
            }
            for extra in ("raw_text", "image", "uri", "path"):
                response = client.post(
                    f"/api/people/{person['id']}/medications/ocr-preview",
                    json={"envelope": envelope, extra: "SENTINEL"},
                )
                self.assertIn(response.status_code, (400, 422), extra)
            response = client.post(
                f"/api/people/{person['id']}/medications/preview",
                json={"product_ref": "MFDS-A", "raw_text": "SENTINEL"},
            )
            self.assertEqual(response.status_code, 422)
            response = client.post(
                f"/api/people/{person['id']}/medications",
                json={"product_ref": "MFDS-A", "raw_text": "SENTINEL"},
            )
            self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
