from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.web import create_web_app
from tests.test_app_core import make_dur_db


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_dur_db(self.dur_db)
        self.client = TestClient(create_web_app(self.dur_db, self.personal_db))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mobile_shell_is_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="viewport"', response.text)
        self.assertIn('class="bottom-nav"', response.text)
        self.assertIn("복용", response.text)
        self.assertIn("약 검색", response.text)

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


if __name__ == "__main__":
    unittest.main()
