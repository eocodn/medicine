from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.web import create_web_app
from tests.test_app_core import make_catalog_db, make_dur_db


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.client = TestClient(create_web_app(self.dur_db, self.personal_db, self.catalog_db))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mobile_shell_is_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="viewport"', response.text)
        self.assertIn('class="bottom-nav"', response.text)
        self.assertIn("복용", response.text)
        self.assertIn("약 검색", response.text)
        self.assertIn('id="include-inactive"', response.text)

    def test_product_search_can_include_inactive_permit_records(self) -> None:
        default = self.client.get("/api/products", params={"q": "과거취하약"})
        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.json(), [])

        response = self.client.get(
            "/api/products",
            params={"q": "과거취하약", "include_inactive": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["permit_status"], "withdrawn")
        self.assertEqual(response.json()[0]["permit_status_name"], "취하")

    def test_product_search_returns_service_unavailable_without_full_catalog(self) -> None:
        missing_catalog = self.catalog_db.with_name("missing-catalog.sqlite")
        client = TestClient(create_web_app(self.dur_db, self.personal_db.with_name("other.sqlite"), missing_catalog))

        health = client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["full_catalog"])

        response = client.get("/api/products", params={"q": "약A"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("catalog database not available", response.json()["detail"])

    def test_person_search_preview_add_and_log_flow(self) -> None:
        person_response = self.client.post(
            "/api/people",
            json={
                "name": "테스트",
                "birth_date": "2010-01-10",
                "sex": "female",
                "pregnancy_status": "pregnant",
            },
        )
        self.assertEqual(person_response.status_code, 201)
        person = person_response.json()

        search = self.client.get("/api/products", params={"q": "약B"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["product_code"], "P-B")

        self.client.post(
            f"/api/people/{person['id']}/medications",
            json={"product_code": "P-A", "schedule_times": ["08:00"]},
        )
        preview = self.client.post(
            f"/api/people/{person['id']}/medications/preview",
            json={"product_code": "P-B"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("combination_contraindication", {r["type"] for r in preview.json()["risks"]})

        added = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={"product_code": "P-B", "dosage_text": "1정", "schedule_times": ["20:00"]},
        )
        self.assertEqual(added.status_code, 201)
        medication = added.json()

        logged = self.client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"status": "taken"},
        )
        self.assertEqual(logged.status_code, 201)

        dashboard = self.client.get(f"/api/people/{person['id']}/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        body = dashboard.json()
        self.assertEqual(len(body["medications"]), 2)
        self.assertEqual(len(body["recent_logs"]), 1)

    def test_structured_prescription_and_daily_plan_api(self) -> None:
        person = self.client.post(
            "/api/people",
            json={"name": "일정", "birth_date": "1990-01-01", "sex": "female", "pregnancy_status": "not_pregnant"},
        ).json()

        search = self.client.get("/api/products", params={"q": "전체카탈로그약B"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["product_ref"], "MFDS-B")

        added = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={
                "product_ref": "MFDS-B",
                "dose_amount": 1,
                "dose_unit": "정",
                "frequency_per_day": 2,
                "meal_relation": "after_meal",
                "administration_route": "oral",
                "prescription_days": 2,
                "start_date": "2026-08-10",
                "schedule_times": ["08:00", "20:00"],
            },
        )
        self.assertEqual(added.status_code, 201)
        self.assertEqual(added.json()["end_date"], "2026-08-11")

        plan = self.client.get(f"/api/people/{person['id']}/daily-plan", params={"date": "2026-08-10"})
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(len(plan.json()["doses"]), 2)

        instance_id = plan.json()["doses"][0]["id"]
        completed = self.client.post(
            f"/api/dose-instances/{instance_id}",
            json={"status": "taken", "occurred_at": "2026-08-10T08:01:00+09:00"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "taken")

        dashboard = self.client.get(f"/api/people/{person['id']}/dashboard", params={"date": "2026-08-10"})
        self.assertEqual(dashboard.json()["daily_plan"]["summary"]["taken"], 1)


if __name__ == "__main__":
    unittest.main()
