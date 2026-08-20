from __future__ import annotations

import gc
import json
import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from pathlib import Path

from medicine_app.mobile_api import MobileApi
from tests.test_app_core import make_canonical_db


class MobileApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        self.api = MobileApi(self.canonical_db, self.personal_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        raw = self.api.request(method, path, json.dumps(body, ensure_ascii=False) if body is not None else "")
        envelope = json.loads(raw)
        return envelope["status"], envelope["body"]

    def test_prepare_for_seal_checkpoints_personal_database(self) -> None:
        self.request("POST", "/api/people", {
            "name": "암호화",
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable",
        })

        self.api.prepare_for_seal()

        with closing(sqlite3.connect(self.personal_db)) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM people").fetchone()[0], 1)
        wal = Path(str(self.personal_db) + "-wal")
        self.assertTrue(not wal.exists() or wal.stat().st_size == 0)

    def test_prepare_for_seal_closes_checkpoint_connection(self) -> None:
        gc.collect()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self.api.prepare_for_seal()
            gc.collect()
        leaked = [warning for warning in caught if "unclosed database" in str(warning.message)]
        self.assertEqual(leaked, [])

    def test_request_access_distinguishes_reference_reads_personal_reads_and_writes(self) -> None:
        self.assertEqual(self.api.request_access("GET", "/api/health"), "reference")
        self.assertEqual(self.api.request_access("GET", "/api/products?q=%EC%95%BDA"), "reference")
        self.assertEqual(self.api.request_access("GET", "/api/people"), "personal_read")
        self.assertEqual(
            self.api.request_access("POST", "/api/people/example/medications/preview"),
            "personal_read",
        )
        self.assertEqual(
            self.api.request_access("GET", "/api/medications/example/history"),
            "personal_read",
        )
        self.assertEqual(self.api.request_access("GET", "/api/people/example/dashboard"), "personal_write")
        self.assertEqual(self.api.request_access("POST", "/api/dose-instances/example"), "personal_write")

    def test_personal_read_scope_rejects_write_connections(self) -> None:
        with self.api.service.personal_read_only():
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                with self.api.service._personal(write_lock=True):
                    pass

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
            frequency_per_day=1, start_date="2026-08-10", schedule_times=["08:00"], long_term=True,
        )
        _, plan = self.request("GET", f"/api/people/{person['id']}/daily-plan?date=2026-08-10")
        instance = plan["doses"][0]

        status, completed = self.request("POST", f"/api/dose-instances/{instance['id']}", {
            "status": "taken", "occurred_at": "2026-08-10T08:05:00+09:00",
        })
        self.assertEqual(status, 200)
        self.assertEqual(completed["status"], "taken")
        self.assertNotIn("dose_state", completed)
        self.assertEqual(len(completed["recent_logs"]), 1)
        self.assertEqual(completed["recent_logs"][0]["dose_instance_id"], instance["id"])

        status, legacy = self.request("POST", f"/api/medications/{instance['medication_id']}/logs", {
            "status": "taken",
        })
        self.assertEqual(status, 404)
        self.assertEqual(legacy["detail"], "route not found")

        status, canceled = self.request("DELETE", f"/api/dose-instances/{instance['id']}/completion")
        self.assertEqual(status, 200)
        self.assertEqual(canceled["status"], "planned")
        self.assertIsNone(canceled["completed_at"])
        self.assertNotIn("dose_state", canceled)
        self.assertEqual(canceled["recent_logs"], [])

        _, dashboard = self.request("GET", f"/api/people/{person['id']}/dashboard?date=2026-08-10")
        self.assertEqual(dashboard["daily_plan"]["doses"][0]["status"], "planned")
        self.assertEqual(dashboard["recent_logs"], [])

    def test_prn_intake_route_records_actual_use_and_enforces_daily_maximum(self) -> None:
        _, person = self.request("POST", "/api/people", {
            "name": "필요시", "birth_date": "1990-01-01", "sex": "male",
            "pregnancy_status": "not_applicable", "lactation_status": "not_applicable",
        })
        medication = self.api.service.add_medication(
            person["id"], product_ref="MFDS-A", as_needed=True, prn_max_per_day=1,
            long_term=True, start_date="2026-08-10",
        )

        status, recorded = self.request(
            "POST", f"/api/medications/{medication['id']}/prn-intakes",
            {"occurred_at": "2026-08-10T12:00:00+09:00"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(recorded["status"], "taken")
        self.assertEqual(len(recorded["recent_logs"]), 1)
        status, blocked = self.request(
            "POST", f"/api/medications/{medication['id']}/prn-intakes",
            {"occurred_at": "2026-08-10T18:00:00+09:00"},
        )
        self.assertEqual(status, 400)
        self.assertIn("maximum", blocked["detail"])

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
                "product_ref": "MFDS-B",
                "prescription_days": 29,
                "request_id": "mobile-api-warning",
            },
        )
        self.assertEqual(status, 409)
        self.assertTrue(blocked["confirmation_required"])
        self.assertTrue(blocked["warning_token"])

        status, created = self.request(
            "POST", f"/api/people/{person['id']}/medications", {
                "product_ref": "MFDS-B",
                "prescription_days": 29,
                "request_id": "mobile-api-warning",
                "acknowledge_warnings": True,
                "warning_token": blocked["warning_token"],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["product_name"], "전체카탈로그약B")

        status, history = self.request("GET", f"/api/medications/{created['id']}/history")
        self.assertEqual(status, 200)
        self.assertEqual(history[-1]["action"], "create")

    def test_person_profile_update_and_delete_routes(self) -> None:
        status, person = self.request("POST", "/api/people", {
            "name": "프로필",
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant",
            "lactation_status": "not_breastfeeding",
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

    def test_dashboard_reassesses_active_medications_after_profile_update(self) -> None:
        _, person = self.request("POST", "/api/people", {
            "name": "현재 DUR",
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant",
            "lactation_status": "not_breastfeeding",
        })
        self.api.service.add_medication(person["id"], product_ref="MFDS-B", prescription_days=7)

        _, before = self.request("GET", f"/api/people/{person['id']}/dashboard?date=2026-08-11")
        self.assertFalse(before["medications"][0]["dur_alert"])

        status, _ = self.request("PATCH", f"/api/people/{person['id']}", {
            "name": "현재 DUR",
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "pregnant",
            "lactation_status": "not_breastfeeding",
        })
        self.assertEqual(status, 200)
        _, after = self.request("GET", f"/api/people/{person['id']}/dashboard?date=2026-08-11")

        medication = after["medications"][0]
        self.assertTrue(medication["dur_alert"])
        self.assertIn(
            "pregnancy_contraindication",
            {risk["type"] for risk in medication["current_assessment"]["risks"]},
        )


if __name__ == "__main__":
    unittest.main()
