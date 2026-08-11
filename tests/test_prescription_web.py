from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.web import create_web_app
from tests.test_app_core import make_catalog_db, make_dur_db


class PrescriptionWebApiTest(unittest.TestCase):
    """HTTP contracts for quantitative prescription and revision workflows.

    These tests intentionally exercise the web boundary rather than calling the
    service directly.  The existing test fixtures include a 28-day duration
    caution for P-B, which gives us a deterministic threshold-exceeding draft.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.client = TestClient(create_web_app(self.dur_db, self.personal_db, self.catalog_db))

        person_response = self.client.post(
            "/api/people",
            json={
                "name": "처방 사용자",
                "birth_date": "1990-01-01",
                "sex": "female",
                "pregnancy_status": "not_pregnant",
            },
        )
        self.assertEqual(person_response.status_code, 201)
        self.person = person_response.json()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _draft(self, **overrides: object) -> dict:
        draft = {
            "product_ref": "MFDS-B",
            "dose_amount": 1,
            "dose_unit": "정",
            "frequency_per_day": 2,
            "meal_relation": "after_meal",
            "administration_route": "oral",
            "prescription_days": 29,
            "start_date": "2026-08-10",
            "schedule_times": ["08:00", "20:00"],
        }
        draft.update(overrides)
        return draft

    def _create(self, **overrides: object):
        return self.client.post(
            f"/api/people/{self.person['id']}/medications",
            json=self._draft(**overrides),
        )

    def test_preview_accepts_complete_draft_and_returns_quantitative_checks(self) -> None:
        response = self.client.post(
            f"/api/people/{self.person['id']}/medications/preview",
            json=self._draft(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("quantitative_checks", body)
        checks = body["quantitative_checks"]
        self.assertEqual(checks["duration"]["result"], "exceeded")
        self.assertEqual(checks["duration"]["requested_days"], 29)
        self.assertEqual(checks["duration"]["maximum_days"], 28)
        self.assertEqual(checks["dose"]["result"], "not_applicable")
        self.assertTrue(body["warning_token"])

    def test_create_requires_acknowledgement_for_threshold_exceedance(self) -> None:
        blocked = self._create(request_id="threshold-1")

        self.assertEqual(blocked.status_code, 409)
        blocked_body = blocked.json()
        self.assertTrue(blocked_body["confirmation_required"])
        self.assertEqual(blocked_body["request_id"], "threshold-1")
        self.assertEqual(
            self.client.get(f"/api/people/{self.person['id']}/dashboard").json()["medications"],
            [],
        )

        acknowledged = self._create(
            request_id="threshold-1",
            acknowledge_warnings=True,
            warning_token=blocked_body["warning_token"],
        )

        self.assertEqual(acknowledged.status_code, 201)
        medication = acknowledged.json()
        self.assertEqual(medication["prescription_days"], 29)
        self.assertTrue(medication["assessment"]["acknowledged"])

    def test_repeated_request_id_returns_the_same_created_medication(self) -> None:
        draft = self._draft(
            product_ref="MFDS-A",
            prescription_days=5,
            schedule_times=["08:00", "20:00"],
            request_id="retry-safe-1",
        )
        first = self.client.post(
            f"/api/people/{self.person['id']}/medications",
            json=draft,
        )
        second = self.client.post(
            f"/api/people/{self.person['id']}/medications",
            json=draft,
        )

        self.assertIn(first.status_code, (200, 201))
        self.assertIn(second.status_code, (200, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])
        medications = self.client.get(f"/api/people/{self.person['id']}/dashboard").json()["medications"]
        self.assertEqual(len(medications), 1)

    def test_patch_uses_expected_revision_and_requires_warning_acknowledgement(self) -> None:
        created = self._create(
            product_ref="MFDS-B",
            frequency_per_day=1,
            prescription_days=5,
            schedule_times=["08:00"],
        )
        self.assertEqual(created.status_code, 201)
        medication = created.json()
        self.assertIn("revision", medication)

        blocked = self.client.patch(
            f"/api/medications/{medication['id']}",
            json={"expected_revision": medication["revision"], "prescription_days": 29},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(blocked.json()["confirmation_required"])

        updated = self.client.patch(
            f"/api/medications/{medication['id']}",
            json={
                "expected_revision": medication["revision"],
                "prescription_days": 29,
                "acknowledge_warnings": True,
                "warning_token": blocked.json()["warning_token"],
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], medication["revision"] + 1)
        self.assertEqual(updated.json()["prescription_days"], 29)

    def test_get_medication_revision_history_exposes_each_revision(self) -> None:
        created = self._create(
            product_ref="MFDS-A",
            frequency_per_day=1,
            prescription_days=5,
            schedule_times=["08:00"],
        ).json()
        self.assertIn("id", created)
        self.assertIn("revision", created)
        updated = self.client.patch(
            f"/api/medications/{created['id']}",
            json={
                "expected_revision": created["revision"],
                "schedule_times": ["09:00"],
            },
        )
        self.assertEqual(updated.status_code, 200)

        response = self.client.get(f"/api/medications/{created['id']}/history")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        history = body if isinstance(body, list) else body["revisions"]
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual([entry["revision"] for entry in history], [1, 2])

    def test_schedule_edit_preserves_completed_dose_history(self) -> None:
        created = self._create(
            product_ref="MFDS-A",
            prescription_days=5,
            schedule_times=["08:00", "20:00"],
        ).json()
        self.assertIn("id", created)
        self.assertIn("revision", created)
        plan = self.client.get(
            f"/api/people/{self.person['id']}/daily-plan",
            params={"date": "2026-08-10"},
        ).json()
        completed = next(dose for dose in plan["doses"] if dose["scheduled_time"] == "08:00")
        completion = self.client.post(
            f"/api/dose-instances/{completed['id']}",
            json={"status": "taken", "occurred_at": "2026-08-10T08:05:00+09:00"},
        )
        self.assertEqual(completion.status_code, 200)

        updated = self.client.patch(
            f"/api/medications/{created['id']}",
            json={
                "expected_revision": created["revision"],
                "schedule_times": ["09:00", "21:00"],
            },
        )
        self.assertEqual(updated.status_code, 200)

        refreshed = self.client.get(
            f"/api/people/{self.person['id']}/daily-plan",
            params={"date": "2026-08-10"},
        ).json()
        self.assertEqual(
            next(dose for dose in refreshed["doses"] if dose["id"] == completed["id"])["status"],
            "taken",
        )
        self.assertEqual(
            {dose["scheduled_time"] for dose in refreshed["doses"] if dose["status"] == "planned"},
            {"09:00", "21:00"},
        )

    def test_completed_dose_can_be_canceled_back_to_planned(self) -> None:
        created = self._create(
            product_ref="MFDS-A",
            prescription_days=5,
            schedule_times=["08:00", "20:00"],
        ).json()
        self.assertIn("id", created)
        plan = self.client.get(
            f"/api/people/{self.person['id']}/daily-plan",
            params={"date": "2026-08-10"},
        ).json()
        instance = next(dose for dose in plan["doses"] if dose["scheduled_time"] == "08:00")

        completed = self.client.post(
            f"/api/dose-instances/{instance['id']}",
            json={"status": "taken", "occurred_at": "2026-08-10T08:05:00+09:00"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "taken")

        canceled = self.client.delete(f"/api/dose-instances/{instance['id']}/completion")
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["status"], "planned")
        self.assertIsNone(canceled.json()["completed_at"])

        dashboard = self.client.get(
            f"/api/people/{self.person['id']}/dashboard",
            params={"date": "2026-08-10"},
        ).json()
        restored = next(dose for dose in dashboard["daily_plan"]["doses"] if dose["id"] == instance["id"])
        self.assertEqual(restored["status"], "planned")
        self.assertEqual(dashboard["recent_logs"], [])

        canceled_again = self.client.delete(f"/api/dose-instances/{instance['id']}/completion")
        self.assertEqual(canceled_again.status_code, 200)
        self.assertEqual(canceled_again.json()["status"], "planned")


if __name__ == "__main__":
    unittest.main()
