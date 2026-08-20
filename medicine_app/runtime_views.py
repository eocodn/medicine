from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, Callable

from .assessment import (
    assess_current_medication,
    assess_medication,
    bind_warning_token,
    has_dur_alert,
    has_split_prohibition,
    resolve_current_products,
)
from .medication_policy import dur_review_required
from .planning import materialize_daily_plan, sort_medications_by_time
from .prescriptions import draft_hash, normalize_draft
from .safety import APP_TIMEZONE


def target_date(value: str | date | None) -> date:
    if value is None:
        return datetime.now(APP_TIMEZONE).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def active_medications_for_date(medications: list[dict], target: date) -> list[dict]:
    return [
        medication
        for medication in medications
        if medication.get("active")
        and (not medication.get("end_date") or date.fromisoformat(medication["end_date"]) >= target)
    ]


def list_dose_logs_from_connection(
    con: sqlite3.Connection,
    person_id: str,
    limit: int,
) -> list[dict]:
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


def _apply_current_assessment_fields(medication: dict, assessment: dict) -> None:
    medication["current_assessment"] = assessment
    medication["permit_status"] = assessment.get("permit_status")
    medication["permit_status_name"] = assessment.get("permit_status_name")
    medication["permit_status_changed_at"] = assessment.get("permit_status_changed_at")
    medication["dur_alert"] = has_dur_alert(assessment)
    medication["split_prohibited"] = has_split_prohibition(assessment)
    medication["dur_review_required"] = dur_review_required(assessment)
    medication["review_required"] = bool(assessment.get("requires_review"))


def get_dashboard(app: Any, person_id: str, target_value: str | date | None, uuid_factory: Callable[[], str]) -> dict:
    target = target_date(target_value)
    with app._personal() as personal_con:
        person = app._get_person_from_connection(personal_con, person_id)
        all_medications = app._list_medications_from_connection(
            personal_con,
            person_id,
            active_only=False,
        )
        medications = active_medications_for_date(all_medications, target)
        product_cache: dict[str, dict] = {}
        with app._canonical() as canonical_con:
            current_products = resolve_current_products(
                app,
                all_medications,
                canonical_con=canonical_con,
                product_cache=product_cache,
            )
            for medication in medications:
                assessment = assess_current_medication(
                    app,
                    personal_con,
                    person,
                    medication,
                    as_of=target,
                    canonical_con=canonical_con,
                    current_products=current_products,
                    product_cache=product_cache,
                )
                _apply_current_assessment_fields(medication, assessment)
        medications = sort_medications_by_time(medications, target)
        return {
            "person": person,
            "medications": medications,
            "recent_logs": list_dose_logs_from_connection(personal_con, person_id, 20),
            "daily_plan": materialize_daily_plan(
                personal_con,
                person_id,
                medications,
                target,
                uuid_factory,
            ),
        }


def get_daily_plan(app: Any, person_id: str, target_value: str | date | None, uuid_factory: Callable[[], str]) -> dict:
    target = target_date(target_value)
    with app._personal() as con:
        app._get_person_from_connection(con, person_id)
        medications = active_medications_for_date(
            app._list_medications_from_connection(con, person_id, active_only=True),
            target,
        )
        medications = sort_medications_by_time(medications, target)
        return materialize_daily_plan(con, person_id, medications, target, uuid_factory)


def with_recent_dose_logs(app: Any, dose: dict) -> dict:
    result = dict(dose)
    person_id = result.get("person_id")
    if person_id:
        with app._personal() as con:
            result["recent_logs"] = list_dose_logs_from_connection(con, str(person_id), 20)
    return result


def list_dose_logs(app: Any, person_id: str, limit: int) -> list[dict]:
    with app._personal() as con:
        app._get_person_from_connection(con, person_id)
        return list_dose_logs_from_connection(con, person_id, limit)


def preview_medication(app: Any, person_id: str, draft_or_ref: Any, as_of: date | None = None) -> dict:
    raw = dict(draft_or_ref) if isinstance(draft_or_ref, dict) else {"product_ref": draft_or_ref}
    product_ref = raw.pop("product_ref", None) or raw.pop("product_code", None)
    manual_name = raw.pop("manual_name", None)
    ingredient_name = raw.pop("ingredient_name", None)
    draft = normalize_draft(raw)
    product_cache: dict[str, dict] = {}
    with app._canonical() as canonical_con:
        product = app._resolve_product(
            product_ref,
            manual_name,
            ingredient_name,
            canonical_con=canonical_con,
        )
        with app._personal() as con:
            person = app._get_person_from_connection(con, person_id)
            current_medications = app._list_medications_from_connection(
                con,
                person_id,
                active_only=False,
            )
            current_products = resolve_current_products(
                app,
                current_medications,
                canonical_con=canonical_con,
                product_cache=product_cache,
            )
            assessment = assess_medication(
                app,
                con,
                person,
                product,
                draft,
                False,
                as_of=as_of,
                preloaded_current=current_products,
                canonical_con=canonical_con,
                product_cache=product_cache,
            )
            current_count = len(current_medications)
    fingerprint = draft_hash(person_id, product, draft)
    warning_token = bind_warning_token(assessment, fingerprint)
    return {
        "person": person,
        "product": product,
        "draft": draft,
        "current_medication_count": current_count,
        "risks": assessment["risks"],
        "review_items": assessment.get("review_items") or [],
        "dur_checks": assessment["dur_checks"],
        "quantitative_checks": {
            "duration": assessment["duration"],
            "dose": assessment["dose"],
        },
        "warning_token": warning_token,
        "coverage": assessment["coverage"],
    }
