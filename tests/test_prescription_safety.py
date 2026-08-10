from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from zoneinfo import ZoneInfo

from medicine_app.core import ConfirmationRequired, MedicationApp


APP_TZ = ZoneInfo("Asia/Seoul")


def make_dur_db(path: Path) -> None:
    """Create the smallest DUR fixture needed by the prescription contract."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE product_dur (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            ingredient_name TEXT,
            ingredient_code TEXT,
            product_name TEXT,
            product_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_code TEXT,
            paired_product_name TEXT,
            paired_product_code TEXT,
            rule_value TEXT,
            details TEXT,
            notice_no TEXT,
            notice_date TEXT
        );
        CREATE TABLE product_catalog (
            product_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            ingredient_code TEXT,
            ingredient_name TEXT
        );
        """
    )
    rows = [
        (
            "duration:P-SAFE",
            1,
            "duration_caution",
            "example",
            "ING-SAFE",
            "정량비교약",
            "P-SAFE",
            "28",
            "최대 투여기간은 28일입니다.",
        ),
        (
            "dose:P-SAFE",
            2,
            "dose_caution",
            "example",
            "ING-SAFE",
            "정량비교약",
            "P-SAFE",
            "예시성분 10mg",
            json.dumps(
                {
                    "1일최대 투여기준량": "10",
                    "점검기준 성분함량 (총함량)": "5",
                },
                ensure_ascii=False,
            ),
        ),
        (
            "duration:P-UNKNOWN",
            3,
            "duration_caution",
            "unknown",
            "ING-UNKNOWN",
            "비교불가약",
            "P-UNKNOWN",
            "복용기간 확인 필요",
            "수치화할 수 없는 기준입니다.",
        ),
        (
            "dose:P-UNKNOWN",
            4,
            "dose_caution",
            "unknown",
            "ING-UNKNOWN",
            "비교불가약",
            "P-UNKNOWN",
            "적합 단위 확인 필요",
            "수치화할 수 없는 기준입니다.",
        ),
    ]
    con.executemany(
        """
        INSERT INTO product_dur(
            dataset_key,source_row,category,ingredient_name,ingredient_code,
            product_name,product_code,rule_value,details
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    con.executemany(
        "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
        [("P-SAFE", "정량비교약", "ING-SAFE", "example"), ("P-UNKNOWN", "비교불가약", "ING-UNKNOWN", "unknown")],
    )
    con.commit()
    con.close()


def make_catalog_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE products (
            item_seq TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            manufacturer TEXT,
            ingredient_name TEXT,
            dosage_form TEXT,
            edi_code TEXT,
            permit_date TEXT,
            cancel_date TEXT,
            cancel_name TEXT,
            permit_status TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )
    con.executemany(
        """
        INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_name,dosage_form,
            edi_code,permit_date,cancel_date,cancel_name,permit_status,source,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            ("MFDS-SAFE", "정량비교약", "예시제약", "example", "정제", "P-SAFE", "2020-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-UNKNOWN", "비교불가약", "예시제약", "unknown", "정제", "P-UNKNOWN", "2020-01-01", None, "정상", "active", "fixture", "{}"),
        ],
    )
    con.commit()
    con.close()


class PrescriptionSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.app = MedicationApp(self.dur_db, self.personal_db, self.catalog_db)
        self.person = self.app.create_person("환자", "1990-01-01", "unknown", "unknown")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _draft(**overrides: object) -> dict:
        draft = {
            "product_ref": "MFDS-SAFE",
            "dose_amount": 5,
            "dose_unit": "mg",
            "frequency_per_day": 1,
            "prescription_days": 7,
            "start_date": "2026-08-10",
            "schedule_times": ["08:00"],
        }
        draft.update(overrides)
        return draft

    @staticmethod
    def _dimension(preview: dict, name: str) -> dict:
        return preview["quantitative_checks"][name]

    def test_full_preview_reports_duration_exceeded_and_dose_within(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=35, dose_amount=5)
        )

        self.assertEqual(self._dimension(preview, "duration")["result"], "exceeded")
        self.assertEqual(self._dimension(preview, "dose")["result"], "within")

    def test_full_preview_reports_duration_within_and_dose_exceeded(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"], self._draft(prescription_days=7, dose_amount=11, frequency_per_day=1)
        )

        self.assertEqual(self._dimension(preview, "duration")["result"], "within")
        self.assertEqual(self._dimension(preview, "dose")["result"], "exceeded")

    def test_countable_dose_uses_product_ingredient_content(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            self._draft(dose_amount=1, dose_unit="정", frequency_per_day=3, schedule_times=[]),
        )

        dose = self._dimension(preview, "dose")
        self.assertEqual(dose["result"], "exceeded")
        self.assertEqual(dose["daily_amount"], 15.0)
        self.assertEqual(dose["maximum_daily_amount"], 10.0)

    def test_full_preview_marks_each_dimension_not_evaluable_with_reason(self) -> None:
        preview = self.app.preview_medication(
            self.person["id"],
            self._draft(
                product_ref="MFDS-UNKNOWN",
                prescription_days=None,
                dose_amount=1,
                dose_unit="tablet",
            ),
        )

        for name in ("duration", "dose"):
            dimension = self._dimension(preview, name)
            self.assertEqual(dimension["result"], "not_evaluable")
            self.assertTrue(dimension["reason"])

    def test_fractional_frequency_and_duration_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.app.add_medication(
                self.person["id"], **self._draft(frequency_per_day=1.5), request_id="fraction-frequency"
            )
        with self.assertRaises(ValueError):
            self.app.add_medication(
                self.person["id"], **self._draft(prescription_days=1.5), request_id="fraction-days"
            )

    def test_unsupported_source_unit_is_not_evaluable(self) -> None:
        con = sqlite3.connect(self.dur_db)
        con.execute(
            "UPDATE product_dur SET rule_value='10 tablets' WHERE product_code='P-SAFE' AND category='dose_caution'"
        )
        con.commit()
        con.close()

        preview = self.app.preview_medication(
            self.person["id"], self._draft(dose_amount=5, dose_unit="정")
        )

        self.assertEqual(self._dimension(preview, "dose")["result"], "not_evaluable")

    def test_concurrent_schema_initialization_is_serialized(self) -> None:
        concurrent_db = Path(self.tmp.name) / "concurrent.sqlite"
        con = sqlite3.connect(concurrent_db)
        con.executescript(
            """
            CREATE TABLE people (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, birth_date TEXT NOT NULL,
                sex TEXT NOT NULL, pregnancy_status TEXT NOT NULL, notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE medications (
                id TEXT PRIMARY KEY, person_id TEXT NOT NULL, product_code TEXT,
                product_name TEXT NOT NULL, ingredient_code TEXT, ingredient_name TEXT,
                dosage_text TEXT, start_date TEXT, end_date TEXT, active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'dur_search', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE medication_schedules (
                id TEXT PRIMARY KEY, medication_id TEXT NOT NULL, time_of_day TEXT NOT NULL,
                dose_text TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE dose_logs (
                id TEXT PRIMARY KEY, medication_id TEXT NOT NULL, person_id TEXT NOT NULL,
                status TEXT NOT NULL, occurred_at TEXT NOT NULL, note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        con.commit()
        con.close()
        barrier = Barrier(12)

        def initialize(_: int) -> MedicationApp:
            barrier.wait()
            return MedicationApp(self.dur_db, concurrent_db, self.catalog_db)

        with ThreadPoolExecutor(max_workers=12) as executor:
            apps = list(executor.map(initialize, range(12)))

        self.assertEqual(len(apps), 12)

    def test_exceeded_create_requires_acknowledgement(self) -> None:
        draft = self._draft(prescription_days=35, dose_amount=11)
        with self.assertRaisesRegex(ValueError, "acknowledg"):
            self.app.add_medication(
                self.person["id"],
                **draft,
                request_id="create-exceeded-1",
                acknowledge_warnings=False,
            )
        self.assertEqual(self.app.list_medications(self.person["id"]), [])

    def test_exceeded_create_succeeds_only_after_explicit_acknowledgement(self) -> None:
        draft = self._draft(prescription_days=35, dose_amount=11)
        with self.assertRaises(ConfirmationRequired) as warning:
            self.app.add_medication(
                self.person["id"], **draft, request_id="create-exceeded-2"
            )
        medication = self.app.add_medication(
            self.person["id"],
            **draft,
            request_id="create-exceeded-2",
            acknowledge_warnings=True,
            warning_token=warning.exception.assessment["draft_fingerprint"],
        )

        self.assertEqual(medication["assessment"]["duration"]["result"], "exceeded")
        self.assertEqual(medication["assessment"]["acknowledged"], True)

    def test_create_request_id_is_idempotent_and_rejects_payload_mismatch(self) -> None:
        draft = self._draft()
        kwargs = {
            **draft,
            "request_id": "create-idempotent-1",
            "acknowledge_warnings": False,
        }

        first = self.app.add_medication(self.person["id"], **kwargs)
        retry = self.app.add_medication(self.person["id"], **kwargs)
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(len(self.app.list_medications(self.person["id"])), 1)

        with self.assertRaisesRegex(ValueError, "request_id|payload"):
            self.app.add_medication(
                self.person["id"],
                **{**kwargs, "dose_amount": 6},
            )

    def test_update_requires_expected_revision_and_records_history(self) -> None:
        medication = self.app.add_medication(
            self.person["id"],
            **self._draft(),
            request_id="update-history-create",
        )
        revision = medication["revision"]

        with self.assertRaises((TypeError, ValueError)):
            self.app.update_medication(medication["id"], schedule_times=["09:00"])

        updated = self.app.update_medication(
            medication["id"], expected_revision=revision, schedule_times=["09:00"], dose_amount=6
        )

        self.assertEqual(updated["revision"], revision + 1)
        history = self.app.list_medication_revisions(medication["id"])
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[-1]["revision"], updated["revision"])
        self.assertEqual(history[-1]["action"], "update")

    def test_update_replaces_schedules_and_removes_only_future_planned_instances(self) -> None:
        today = datetime.now(APP_TZ).date()
        medication = self.app.add_medication(
            self.person["id"],
            **self._draft(
                start_date=(today - timedelta(days=1)).isoformat(),
                prescription_days=4,
                frequency_per_day=2,
                schedule_times=["08:00", "20:00"],
            ),
            request_id="schedule-replacement-create",
        )
        completed_day = (today - timedelta(days=1)).isoformat()
        future_day = (today + timedelta(days=1)).isoformat()
        completed_plan = self.app.get_daily_plan(self.person["id"], completed_day)
        future_plan = self.app.get_daily_plan(self.person["id"], future_day)
        completed_instance = completed_plan["doses"][0]
        self.app.record_dose_instance(
            completed_instance["id"], "taken", f"{completed_day}T08:05:00+09:00"
        )
        old_future_ids = {dose["id"] for dose in future_plan["doses"]}

        updated = self.app.update_medication(
            medication["id"], expected_revision=medication["revision"], schedule_times=["09:00"]
        )

        refreshed = self.app.get_daily_plan(self.person["id"], future_day)
        self.assertEqual([dose["scheduled_time"] for dose in refreshed["doses"]], ["09:00"])
        self.assertTrue(old_future_ids.isdisjoint({dose["id"] for dose in refreshed["doses"]}))
        completed_refreshed = self.app.get_daily_plan(self.person["id"], completed_day)
        self.assertEqual(completed_refreshed["doses"][0]["status"], "taken")
        self.assertEqual(len(self.app.list_dose_logs(self.person["id"])), 1)
        self.assertEqual(updated["schedules"][0]["time_of_day"], "09:00")

    def test_stop_records_a_revision(self) -> None:
        medication = self.app.add_medication(
            self.person["id"], **self._draft(), request_id="stop-history-create"
        )

        stopped = self.app.stop_medication(medication["id"], expected_revision=medication["revision"])

        self.assertFalse(stopped["active"])
        self.assertEqual(stopped["revision"], medication["revision"] + 1)
        history = self.app.list_medication_revisions(medication["id"])
        self.assertEqual(history[-1]["action"], "stop")


if __name__ == "__main__":
    unittest.main()
