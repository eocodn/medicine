from __future__ import annotations

import fcntl
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from .persistence import ensure_personal_schema
from .planning import (
    cancel_instance_completion,
    clock_sort_key,
    materialize_daily_plan,
    record_instance,
    sort_medications_by_time,
)
from .prescriptions import draft_hash, normalize_draft
from .products import ProductRepository
from .ocr import OCRReviewStore, preview_ocr, validate_ocr_create
from .assessment import (
    assess_current_medication,
    assess_medication,
    bind_warning_token,
    has_dur_alert,
    requires_acknowledgement,
)
from .profiles import create_person_record, delete_person_record, person_dict, update_person_record
from .safety import APP_TIMEZONE, age_years

DOSE_STATUS_VALUES = {"taken", "skipped"}


def _uuid() -> str:
    return str(uuid.uuid4())


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class ConfirmationRequired(ValueError):
    def __init__(self, request_id: str | None, assessment: dict):
        super().__init__("warning acknowledgement is required")
        self.request_id = request_id
        self.assessment = assessment


class RevisionConflict(ValueError):
    pass


class IdempotencyConflict(ValueError):
    pass


@contextmanager
def _schema_lock(db_path: Path) -> Iterator[None]:
    # SQLite serializes DDL after a connection is established, but concurrent
    # legacy migrations can still race while toggling WAL and adding columns.
    # A retained per-database lock file provides a cross-process boundary.
    lock_path = db_path.with_name(db_path.name + ".schema.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class MedicationApp:
    def __init__(
        self,
        dur_db: Path | str,
        personal_db: Path | str,
        catalog_db: Path | str | None = None,
    ):
        self.dur_db = Path(dur_db)
        self.personal_db = Path(personal_db)
        self.catalog_db = Path(catalog_db) if catalog_db else None
        if not self.dur_db.exists():
            raise FileNotFoundError(f"DUR database not found: {self.dur_db}")
        self.personal_db.parent.mkdir(parents=True, exist_ok=True)
        self.products = ProductRepository(self.dur_db, self.catalog_db)
        self.ocr_reviews = OCRReviewStore()
        with _schema_lock(self.personal_db):
            with self._personal() as con:
                ensure_personal_schema(con)

    @contextmanager
    def _personal(self, *, write_lock: bool = False) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.personal_db, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        try:
            if write_lock:
                con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def _dur(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self.dur_db.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        try:
            yield con
        finally:
            con.close()

    def create_person(
        self,
        name: str,
        birth_date: str,
        sex: str = "unknown",
        pregnancy_status: str = "unknown",
        lactation_status: str = "unknown",
        notes: str | None = None,
    ) -> dict:
        with self._personal(write_lock=True) as con:
            return create_person_record(
                con, _uuid(), name, birth_date, sex, pregnancy_status, lactation_status, notes
            )

    def update_person(
        self,
        person_id: str,
        name: str,
        birth_date: str,
        sex: str,
        pregnancy_status: str,
        lactation_status: str,
        notes: str | None = None,
    ) -> dict:
        with self._personal(write_lock=True) as con:
            return update_person_record(
                con, person_id, name, birth_date, sex, pregnancy_status, lactation_status, notes
            )

    def delete_person(self, person_id: str) -> dict:
        with self._personal(write_lock=True) as con:
            return delete_person_record(con, person_id)

    def list_people(self) -> list[dict]:
        with self._personal() as con:
            rows = con.execute("SELECT * FROM people ORDER BY rowid").fetchall()
        return [person_dict(row) for row in rows]

    def get_person(self, person_id: str) -> dict:
        with self._personal() as con:
            row = con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        if row is None:
            raise KeyError("person not found")
        return person_dict(row)

    def search_products(self, term: str, limit: int = 30, include_inactive: bool = False) -> list[dict]:
        return self.products.search(term, limit, include_inactive=include_inactive)

    def get_product(self, product_ref: str) -> dict:
        return self.products.get(product_ref)

    def add_medication(
        self,
        person_id: str,
        *,
        product_ref: str | None = None,
        product_code: str | None = None,
        manual_name: str | None = None,
        ingredient_name: str | None = None,
        dosage_text: str | None = None,
        dose_amount: float | None = None,
        dose_unit: str | None = None,
        frequency_per_day: int | None = None,
        meal_relation: str = "unspecified",
        administration_route: str = "oral",
        as_needed: bool = False,
        prescription_days: int | None = None,
        schedule_times: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        request_id: str | None = None,
        acknowledge_warnings: bool = False,
        warning_token: str | None = None,
        ocr_review_token: str | None = None,
        ocr_origin: bool = False,
    ) -> dict:
        product = self._resolve_product(product_ref or product_code, manual_name, ingredient_name, source)
        draft = normalize_draft(dict(
            dosage_text=dosage_text, dose_amount=dose_amount, dose_unit=dose_unit,
            frequency_per_day=frequency_per_day, meal_relation=meal_relation,
            administration_route=administration_route, as_needed=as_needed,
            prescription_days=prescription_days, schedule_times=schedule_times,
            start_date=start_date, end_date=end_date,
        ))
        payload_hash = draft_hash(person_id, product, draft)
        request_id = (request_id or "").strip() or None
        with self._personal(write_lock=True) as con:
            existing = con.execute(
                "SELECT person_id,payload_hash,medication_id FROM medication_requests WHERE request_id=?",
                (request_id,),
            ).fetchone() if request_id else None
            if existing:
                if existing["person_id"] != person_id or existing["payload_hash"] != payload_hash:
                    raise IdempotencyConflict("request_id was already used with a different prescription payload")
                return self._get_medication_from_connection(con, existing["medication_id"])
            validate_ocr_create(self, ocr_review_token, ocr_origin or source == "ocr", person_id, product, draft, request_id)
            person = self._get_person_from_connection(con, person_id)
            assessment = assess_medication(self, con, person, product, draft, acknowledge_warnings)
            expected_warning_token = bind_warning_token(assessment, payload_hash)
            if requires_acknowledgement(assessment) and (
                not acknowledge_warnings or warning_token != expected_warning_token
            ):
                raise ConfirmationRequired(request_id, assessment)
            medication_id = _uuid()
            con.execute(
                """
                INSERT INTO medications(
                    id,person_id,catalog_item_seq,product_code,product_name,ingredient_code,ingredient_name,
                    manufacturer,catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,
                    meal_relation,administration_route,as_needed,prescription_days,
                    start_date,end_date,active,source,revision
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1)
                """,
                (
                    medication_id, person_id, product["catalog_item_seq"], product["product_code"],
                    product["product_name"], product.get("ingredient_code"), product.get("ingredient_name"),
                    product.get("manufacturer"), product["catalog_source"], draft["dosage_text"],
                    draft["dose_amount"], draft["dose_unit"], draft["frequency_per_day"],
                    draft["meal_relation"], draft["administration_route"], int(draft["as_needed"]),
                    draft["prescription_days"], draft["start_date"], draft["end_date"], product["med_source"],
                ),
            )
            self._replace_schedules(con, medication_id, draft["schedule_times"], draft["dosage_text"])
            medication = self._get_medication_from_connection(con, medication_id)
            self._append_revision(
                con, medication, "create", assessment, acknowledge_warnings, request_id, payload_hash
            )
            if request_id:
                con.execute(
                    "INSERT INTO medication_requests(request_id,person_id,payload_hash,medication_id) VALUES(?,?,?,?)",
                    (request_id, person_id, payload_hash, medication_id),
                )
            medication["assessment"] = assessment
            if ocr_review_token:
                self.ocr_reviews.invalidate(ocr_review_token)
            return medication

    def preview_ocr(self, person_id: str, envelope: dict, product_ref: str | None = None) -> dict:
        return preview_ocr(self, person_id, envelope, product_ref)

    ocr_preview = preview_ocr

    def _resolve_product(
        self, resolved_ref: str | None, manual_name: str | None, ingredient_name: str | None, source: str | None
    ) -> dict:
        if resolved_ref:
            product = self.get_product(resolved_ref)
            return {**product, "med_source": source or "catalog_search"}
        name = (manual_name or "").strip()
        if not name:
            raise ValueError("product_ref, product_code or manual_name is required")
        return {
            "product_ref": None, "catalog_item_seq": None, "product_code": None,
            "product_name": name, "ingredient_code": None, "ingredient_name": ingredient_name,
            "manufacturer": None, "dosage_form": None, "catalog_source": "manual",
            "dur_match": False, "med_source": source or "manual",
        }


    def get_medication(self, medication_id: str) -> dict:
        with self._personal() as con:
            return self._get_medication_from_connection(con, medication_id)

    @staticmethod
    def _get_medication_from_connection(con: sqlite3.Connection, medication_id: str) -> dict:
        row = con.execute("SELECT * FROM medications WHERE id=?", (medication_id,)).fetchone()
        if row is None:
            raise KeyError("medication not found")
        schedules = con.execute(
            "SELECT time_of_day,dose_text FROM medication_schedules WHERE medication_id=? ORDER BY time_of_day",
            (medication_id,),
        ).fetchall()
        schedules = sorted(schedules, key=lambda item: clock_sort_key(item["time_of_day"]))
        data = dict(row)
        data["active"] = bool(data["active"])
        data["as_needed"] = bool(data.get("as_needed") or 0)
        data["meal_relation"] = data.get("meal_relation") or "unspecified"
        data["administration_route"] = data.get("administration_route") or "oral"
        data["schedules"] = [dict(item) for item in schedules]
        revision = con.execute(
            "SELECT assessment_json,acknowledged FROM medication_revisions WHERE medication_id=? AND revision=?",
            (medication_id, data.get("revision", 1)),
        ).fetchone()
        if revision and revision["assessment_json"]:
            data["assessment"] = json.loads(revision["assessment_json"])
        return data

    @staticmethod
    def _get_person_from_connection(con: sqlite3.Connection, person_id: str) -> dict:
        row = con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        if row is None:
            raise KeyError("person not found")
        data = dict(row)
        data["age"] = age_years(data["birth_date"])
        return data

    def _list_medications_from_connection(
        self, con: sqlite3.Connection, person_id: str, *, active_only: bool = True, exclude_id: str | None = None
    ) -> list[dict]:
        where = ["person_id=?"]
        params: list = [person_id]
        if active_only:
            where.append("active=1")
        if exclude_id:
            where.append("id<>?")
            params.append(exclude_id)
        rows = con.execute(
            f"SELECT id FROM medications WHERE {' AND '.join(where)} ORDER BY rowid", params
        ).fetchall()
        return [self._get_medication_from_connection(con, row["id"]) for row in rows]

    def list_medications(
        self,
        person_id: str,
        active_only: bool = True,
        as_of: str | date | None = None,
        *,
        include_current_assessment: bool = True,
    ) -> list[dict]:
        target = (
            datetime.now(APP_TIMEZONE).date()
            if as_of is None
            else as_of if isinstance(as_of, date)
            else date.fromisoformat(as_of)
        )
        with self._personal() as con:
            person = self._get_person_from_connection(con, person_id)
            medications = self._list_medications_from_connection(
                con, person_id, active_only=active_only
            )
            if include_current_assessment:
                for medication in medications:
                    if not medication.get("active"):
                        medication["current_assessment"] = None
                        medication["dur_alert"] = False
                        continue
                    current_assessment = assess_current_medication(
                        self,
                        con,
                        person,
                        medication,
                        as_of=target,
                    )
                    medication["current_assessment"] = current_assessment
                    medication["dur_alert"] = has_dur_alert(current_assessment)
        return sort_medications_by_time(medications, target)

    def get_daily_plan(self, person_id: str, target_date: str | date | None = None) -> dict:
        self.get_person(person_id)
        if target_date is None:
            target = datetime.now(APP_TIMEZONE).date()
        elif isinstance(target_date, date):
            target = target_date
        else:
            target = date.fromisoformat(target_date)
        medications = self.list_medications(
            person_id,
            active_only=True,
            as_of=target,
            include_current_assessment=False,
        )
        with self._personal() as con:
            return materialize_daily_plan(con, person_id, medications, target, _uuid)

    def record_dose_instance(
        self,
        instance_id: str,
        status: str,
        occurred_at: str | None = None,
        note: str | None = None,
    ) -> dict:
        if status not in DOSE_STATUS_VALUES:
            raise ValueError("status must be taken or skipped")
        when = occurred_at or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
        datetime.fromisoformat(when)
        with self._personal() as con:
            return record_instance(con, instance_id, status, when, note, _uuid)

    def cancel_dose_instance(self, instance_id: str) -> dict:
        with self._personal() as con:
            return cancel_instance_completion(con, instance_id)

    def update_medication(
        self,
        medication_id: str,
        *,
        expected_revision: int,
        acknowledge_warnings: bool = False,
        warning_token: str | None = None,
        **changes,
    ) -> dict:
        before = self.get_medication(medication_id)
        product = self._resolve_product(
            before.get("catalog_item_seq") or before.get("product_code"),
            before["product_name"] if before.get("catalog_source") == "manual" else None,
            before.get("ingredient_name"), before.get("source"),
        )
        allowed = {
            "dosage_text", "dose_amount", "dose_unit", "frequency_per_day", "meal_relation",
            "administration_route", "as_needed", "prescription_days", "schedule_times",
            "start_date", "end_date",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported medication fields: {', '.join(sorted(unknown))}")
        with self._personal(write_lock=True) as con:
            current = self._get_medication_from_connection(con, medication_id)
            if current["revision"] != int(expected_revision):
                raise RevisionConflict(
                    f"expected revision {expected_revision}, current revision is {current['revision']}"
                )
            values = {
                "dosage_text": current.get("dosage_text"), "dose_amount": current.get("dose_amount"),
                "dose_unit": current.get("dose_unit"), "frequency_per_day": current.get("frequency_per_day"),
                "meal_relation": current.get("meal_relation"),
                "administration_route": current.get("administration_route"),
                "as_needed": current.get("as_needed"), "prescription_days": current.get("prescription_days"),
                "schedule_times": [item["time_of_day"] for item in current["schedules"]],
                "start_date": current.get("start_date"), "end_date": current.get("end_date"),
            }
            values.update(changes)
            if "schedule_times" in changes and "frequency_per_day" not in changes:
                values["frequency_per_day"] = None
            if ("prescription_days" in changes or "start_date" in changes) and "end_date" not in changes:
                values["end_date"] = None
            draft = normalize_draft(values)
            payload_hash = draft_hash(current["person_id"], product, draft)
            person = self._get_person_from_connection(con, current["person_id"])
            assessment = assess_medication(
                self,
                con, person, product, draft, acknowledge_warnings, exclude_medication_id=medication_id
            )
            expected_warning_token = bind_warning_token(assessment, payload_hash)
            if requires_acknowledgement(assessment) and (
                not acknowledge_warnings or warning_token != expected_warning_token
            ):
                raise ConfirmationRequired(None, assessment)
            next_revision = current["revision"] + 1
            result = con.execute(
                """
                UPDATE medications SET dosage_text=?,dose_amount=?,dose_unit=?,frequency_per_day=?,
                    meal_relation=?,administration_route=?,as_needed=?,prescription_days=?,
                    start_date=?,end_date=?,revision=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND revision=?
                """,
                (
                    draft["dosage_text"], draft["dose_amount"], draft["dose_unit"],
                    draft["frequency_per_day"], draft["meal_relation"], draft["administration_route"],
                    int(draft["as_needed"]), draft["prescription_days"], draft["start_date"],
                    draft["end_date"], next_revision, medication_id, expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise RevisionConflict("medication revision changed during update")
            self._replace_schedules(con, medication_id, draft["schedule_times"], draft["dosage_text"])
            con.execute(
                "DELETE FROM dose_instances WHERE medication_id=? AND status='planned' AND scheduled_date>=?",
                (medication_id, datetime.now(APP_TIMEZONE).date().isoformat()),
            )
            updated = self._get_medication_from_connection(con, medication_id)
            self._append_revision(
                con, updated, "update", assessment, acknowledge_warnings, None, payload_hash
            )
            updated["assessment"] = assessment
            return updated

    def stop_medication(self, medication_id: str, *, expected_revision: int) -> dict:
        with self._personal(write_lock=True) as con:
            current = self._get_medication_from_connection(con, medication_id)
            if current["revision"] != int(expected_revision):
                raise RevisionConflict(
                    f"expected revision {expected_revision}, current revision is {current['revision']}"
                )
            next_revision = current["revision"] + 1
            stopped_at = current.get("stopped_at") or datetime.now(APP_TIMEZONE).date().isoformat()
            result = con.execute(
                "UPDATE medications SET active=0,stopped_at=?,revision=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND revision=?",
                (stopped_at, next_revision, medication_id, expected_revision),
            )
            if result.rowcount != 1:
                raise RevisionConflict("medication revision changed during stop")
            con.execute(
                "DELETE FROM dose_instances WHERE medication_id=? AND status='planned' AND scheduled_date>=?",
                (medication_id, datetime.now(APP_TIMEZONE).date().isoformat()),
            )
            stopped = self._get_medication_from_connection(con, medication_id)
            assessment = current.get("assessment") or {}
            self._append_revision(con, stopped, "stop", assessment, False, None, None)
            return stopped

    def deactivate_medication(self, medication_id: str) -> dict:
        current = self.get_medication(medication_id)
        return self.stop_medication(medication_id, expected_revision=current["revision"])

    def list_dose_logs(self, person_id: str, limit: int = 50) -> list[dict]:
        self.get_person(person_id)
        with self._personal() as con:
            rows = con.execute(
                """
                SELECT l.*,
                       COALESCE(l.product_name_snapshot,m.product_name) AS product_name,
                       COALESCE(l.dosage_text_snapshot,m.dosage_text) AS dosage_text
                FROM dose_logs l JOIN medications m ON m.id=l.medication_id
                WHERE l.person_id=? ORDER BY l.occurred_at DESC, l.rowid DESC LIMIT ?
                """,
                (person_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def preview_medication(self, person_id: str, draft_or_ref, as_of: date | None = None) -> dict:
        raw = dict(draft_or_ref) if isinstance(draft_or_ref, dict) else {"product_ref": draft_or_ref}
        product_ref = raw.pop("product_ref", None) or raw.pop("product_code", None)
        product = self._resolve_product(product_ref, raw.pop("manual_name", None), raw.pop("ingredient_name", None), None)
        draft = normalize_draft(raw)
        with self._personal() as con:
            person = self._get_person_from_connection(con, person_id)
            assessment = assess_medication(self, con, person, product, draft, False, as_of=as_of)
            current_count = len(self._list_medications_from_connection(con, person_id))
        fingerprint = draft_hash(person_id, product, draft)
        warning_token = bind_warning_token(assessment, fingerprint)
        return {
            "person": person, "product": product, "draft": draft,
            "current_medication_count": current_count,
            "risks": assessment["risks"],
            "dur_checks": assessment["dur_checks"],
            "quantitative_checks": {
                "duration": assessment["duration"], "dose": assessment["dose"]
            },
            "warning_token": warning_token,
            "coverage": assessment["coverage"],
        }

    @staticmethod
    def _replace_schedules(
        con: sqlite3.Connection, medication_id: str, schedule_times: list[str], dosage_text: str | None
    ) -> None:
        con.execute("DELETE FROM medication_schedules WHERE medication_id=?", (medication_id,))
        con.executemany(
            "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text) VALUES(?,?,?,?)",
            [(_uuid(), medication_id, value, dosage_text) for value in schedule_times],
        )

    @staticmethod
    def _append_revision(
        con: sqlite3.Connection, medication: dict, action: str, assessment: dict,
        acknowledged: bool, request_id: str | None, payload_hash: str | None,
    ) -> None:
        snapshot = {key: value for key, value in medication.items() if key != "assessment"}
        con.execute(
            """INSERT INTO medication_revisions(
                medication_id,revision,action,snapshot_json,assessment_json,acknowledged,request_id,payload_hash
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                medication["id"], medication["revision"], action,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(assessment, ensure_ascii=False, sort_keys=True),
                int(bool(acknowledged)), request_id, payload_hash,
            ),
        )

    def list_medication_revisions(self, medication_id: str) -> list[dict]:
        self.get_medication(medication_id)
        with self._personal() as con:
            rows = con.execute(
                "SELECT * FROM medication_revisions WHERE medication_id=? ORDER BY revision",
                (medication_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["acknowledged"] = bool(item["acknowledged"])
            item["snapshot"] = json.loads(item.pop("snapshot_json"))
            item["assessment"] = json.loads(item.pop("assessment_json")) if item.get("assessment_json") else None
            item.pop("assessment_json", None)
            result.append(item)
        return result
