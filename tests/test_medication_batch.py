from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medicine_app.core import ConfirmationRequired, MedicationApp
from medicine_app.batch_medications import add_medication_batch, preview_medication_batch
from tests.test_app_core import make_canonical_db


class MedicationBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)
        self.app = MedicationApp(self.canonical_db, self.personal_db)
        self.person = self.app.create_person(
            "OCR 묶음", "1990-01-01", "male", "not_applicable", "not_applicable"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def rows() -> list[dict]:
        return [
            {
                "row_id": "row-a",
                "product_ref": "MFDS-A",
                "dose_amount": 1,
                "dose_unit": "정",
                "frequency_per_day": 1,
                "prescription_days": 5,
                "schedule_times": ["08:00"],
                "start_date": "2026-08-13",
                "administration_route": "oral",
            },
            {
                "row_id": "row-b",
                "product_ref": "MFDS-B",
                "dose_amount": 1,
                "dose_unit": "정",
                "frequency_per_day": 1,
                "prescription_days": 5,
                "schedule_times": ["20:00"],
                "start_date": "2026-08-13",
                "administration_route": "oral",
            },
        ]

    def test_preview_checks_interactions_between_rows_in_the_same_batch(self) -> None:
        preview = preview_medication_batch(
            self.app, self.person["id"], self.rows(), operation_id="ocr-batch-1"
        )

        self.assertEqual(len(preview["rows"]), 2)
        self.assertTrue(preview["requires_review"])
        self.assertTrue(preview["warning_token"])
        self.assertTrue(preview["ocr_review_token"])
        first = preview["rows"][0]
        combination = next(
            item for item in first["assessment"]["dur_checks"]
            if item["category"] == "combination_contraindication"
        )
        self.assertEqual(combination["status"], "hit")
        self.assertTrue(any(
            finding.get("related_medication_id") == "batch:row-b"
            for finding in combination["findings"]
        ))
        self.assertEqual(self.app.list_medications(self.person["id"]), [])

    def test_batch_create_is_atomic_requires_one_confirmation_and_is_idempotent(self) -> None:
        rows = self.rows()
        preview = preview_medication_batch(
            self.app, self.person["id"], rows, operation_id="ocr-batch-2"
        )

        with self.assertRaises(ConfirmationRequired):
            add_medication_batch(
                self.app,
                self.person["id"],
                rows,
                request_id="batch-request-1",
                ocr_review_token=preview["ocr_review_token"],
            )
        self.assertEqual(self.app.list_medications(self.person["id"]), [])

        created = add_medication_batch(
            self.app,
            self.person["id"],
            rows,
            request_id="batch-request-1",
            ocr_review_token=preview["ocr_review_token"],
            acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )
        self.assertEqual(len(created["medications"]), 2)
        ids = [item["id"] for item in created["medications"]]
        self.assertEqual(len(self.app.list_medications(self.person["id"])), 2)

        retried = add_medication_batch(
            self.app,
            self.person["id"],
            rows,
            request_id="batch-request-1",
            ocr_review_token=preview["ocr_review_token"],
            acknowledge_warnings=True,
            warning_token=preview["warning_token"],
        )
        self.assertEqual([item["id"] for item in retried["medications"]], ids)
        self.assertEqual(len(self.app.list_medications(self.person["id"])), 2)

    def test_changed_row_after_review_is_rejected_without_partial_write(self) -> None:
        rows = self.rows()
        preview = preview_medication_batch(
            self.app, self.person["id"], rows, operation_id="ocr-batch-3"
        )
        changed = [dict(item) for item in rows]
        changed[1]["dose_amount"] = 2

        with self.assertRaisesRegex(ValueError, "ocr_review_token"):
            add_medication_batch(
                self.app,
                self.person["id"],
                changed,
                request_id="batch-request-changed",
                ocr_review_token=preview["ocr_review_token"],
                acknowledge_warnings=True,
                warning_token=preview["warning_token"],
            )
        self.assertEqual(self.app.list_medications(self.person["id"]), [])

    def test_invalid_regimen_identifies_the_affected_row(self) -> None:
        with self.assertRaisesRegex(ValueError, "row-a"):
            preview_medication_batch(
                self.app, self.person["id"], [{
                    "row_id": "row-a", "product_ref": "MFDS-A",
                    "frequency_per_day": 2, "schedule_times": ["08:00"],
                }], operation_id="ocr-batch-invalid-row",
            )

    def test_row_ids_and_fields_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "row_id"):
            preview_medication_batch(
                self.app,
                self.person["id"],
                [{"row_id": "bad row", "product_ref": "MFDS-A"}],
                operation_id="ocr-batch-4",
            )
        with self.assertRaisesRegex(ValueError, "unsupported batch row fields"):
            preview_medication_batch(
                self.app,
                self.person["id"],
                [{"row_id": "row-a", "product_ref": "MFDS-A", "raw_text": "SECRET"}],
                operation_id="ocr-batch-5",
            )


if __name__ == "__main__":
    unittest.main()
