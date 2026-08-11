from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from medicine_app.mobile_api import MobileApi
from tests.test_app_core import make_catalog_db, make_dur_db


class MobileApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.api = MobileApi(self.dur_db, self.personal_db, self.catalog_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        raw = self.api.request(method, path, json.dumps(body, ensure_ascii=False) if body is not None else "")
        envelope = json.loads(raw)
        return envelope["status"], envelope["body"]

    def test_people_search_dashboard_and_dose_routes_share_the_core(self) -> None:
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertTrue(health["full_catalog"])

        status, person = self.request("POST", "/api/people", {
            "name": "온디바이스",
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable",
        })
        self.assertEqual(status, 201)

        status, products = self.request("GET", "/api/products?q=%EC%95%BDA&limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(products[0]["product_ref"], "MFDS-A")

        status, preview = self.request(
            "POST", f"/api/people/{person['id']}/medications/preview", {"product_ref": "MFDS-A"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["product"]["product_name"], "약A")

        status, dashboard = self.request("GET", f"/api/people/{person['id']}/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["person"]["id"], person["id"])
        self.assertEqual(dashboard["medications"], [])

    def test_dose_completion_can_be_canceled_through_mobile_bridge(self) -> None:
        _, person = self.request("POST", "/api/people", {
            "name": "복용취소",
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable",
        })
        self.api.service.add_medication(
            person["id"], product_ref="MFDS-A", dose_amount=1, dose_unit="정",
            frequency_per_day=1, start_date="2026-08-10", schedule_times=["08:00"],
        )
        _, plan = self.request("GET", f"/api/people/{person['id']}/daily-plan?date=2026-08-10")
        instance = plan["doses"][0]

        status, completed = self.request("POST", f"/api/dose-instances/{instance['id']}", {
            "status": "taken", "occurred_at": "2026-08-10T08:05:00+09:00",
        })
        self.assertEqual(status, 200)
        self.assertEqual(completed["status"], "taken")

        status, legacy = self.request("POST", f"/api/medications/{instance['medication_id']}/logs", {
            "status": "taken",
        })
        self.assertEqual(status, 404)
        self.assertEqual(legacy["detail"], "route not found")

        status, canceled = self.request("DELETE", f"/api/dose-instances/{instance['id']}/completion")
        self.assertEqual(status, 200)
        self.assertEqual(canceled["status"], "planned")
        self.assertIsNone(canceled["completed_at"])

        _, dashboard = self.request("GET", f"/api/people/{person['id']}/dashboard?date=2026-08-10")
        self.assertEqual(dashboard["daily_plan"]["doses"][0]["status"], "planned")
        self.assertEqual(dashboard["recent_logs"], [])

    def test_confirmation_and_validation_errors_keep_http_compatible_envelopes(self) -> None:
        _, person = self.request("POST", "/api/people", {
            "name": "테스트",
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable",
        })

        status, invalid = self.request("POST", "/api/people", {
            "name": "잘못된 요청",
            "birth_date": "1990-01-01",
            "unknown": True,
        })
        self.assertEqual(status, 400)
        self.assertIn("unknown fields", invalid["detail"])

        status, blocked = self.request(
            "POST", f"/api/people/{person['id']}/medications", {
                "product_ref": "MFDS-X",
                "request_id": "mobile-api-warning",
            },
        )
        self.assertEqual(status, 409)
        self.assertTrue(blocked["confirmation_required"])
        self.assertTrue(blocked["warning_token"])

        status, created = self.request(
            "POST", f"/api/people/{person['id']}/medications", {
                "product_ref": "MFDS-X",
                "request_id": "mobile-api-warning",
                "acknowledge_warnings": True,
                "warning_token": blocked["warning_token"],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["product_name"], "비급여전체약X")

        status, history = self.request("GET", f"/api/medications/{created['id']}/history")
        self.assertEqual(status, 200)
        self.assertEqual(history[-1]["action"], "create")

    def test_person_profile_update_and_delete_routes(self) -> None:
        status, person = self.request("POST", "/api/people", {
            "name": "프로필",
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant",
            "lactation_status": "unknown",
        })
        self.assertEqual(status, 201)

        status, updated = self.request("PATCH", f"/api/people/{person['id']}", {
            "name": "프로필",
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant",
            "lactation_status": "breastfeeding",
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["lactation_status"], "breastfeeding")

        status, deleted = self.request("DELETE", f"/api/people/{person['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(deleted, {"id": person["id"], "deleted": True})
        status, people = self.request("GET", "/api/people")
        self.assertEqual(status, 200)
        self.assertEqual(people, [])


if __name__ == "__main__":
    unittest.main()
