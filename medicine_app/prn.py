from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable

from .errors import IdempotencyConflict
from .planning import create_prn_instance, medication_applies_on, prn_taken_count, record_instance
from .safety import APP_TIMEZONE


def _request_payload_hash(medication_id: str, occurred_at: str | None, note: str | None) -> str:
    payload = json.dumps(
        {
            "medication_id": medication_id,
            "occurred_at": occurred_at,
            "note": note,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_prn_intake(
    app: Any,
    medication_id: str,
    occurred_at: str | None,
    note: str | None,
    uuid_factory: Callable[[], str],
    *,
    request_id: str | None = None,
) -> dict:
    if request_id is not None:
        request_id = request_id.strip()
        if not request_id:
            raise ValueError("request_id must not be blank")
    payload_hash = _request_payload_hash(medication_id, occurred_at, note)
    when = occurred_at or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    parsed = datetime.fromisoformat(when)
    target = parsed.astimezone(APP_TIMEZONE).date() if parsed.tzinfo else parsed.date()
    with app._personal(write_lock=True) as con:
        if request_id:
            existing = con.execute(
                """SELECT medication_id,payload_hash,dose_instance_id,state
                   FROM prn_requests WHERE request_id=?""",
                (request_id,),
            ).fetchone()
            if existing is not None:
                if existing["medication_id"] != medication_id or existing["payload_hash"] != payload_hash:
                    raise IdempotencyConflict(
                        "request_id was already used with a different PRN intake payload"
                    )
                if existing["state"] != "active":
                    raise IdempotencyConflict("request_id refers to a canceled PRN intake")
                instance = con.execute(
                    "SELECT * FROM dose_instances WHERE id=?",
                    (existing["dose_instance_id"],),
                ).fetchone()
                if instance is None:
                    raise RuntimeError("PRN request points to a missing dose instance")
                return dict(instance)

        medication = app._get_medication_from_connection(con, medication_id)
        if not medication_applies_on(medication, target):
            raise ValueError("PRN medication is not active on the intake date")
        if not medication.get("as_needed"):
            raise ValueError("medication is not PRN/as_needed")
        maximum = medication.get("prn_max_per_day")
        if maximum is not None and prn_taken_count(con, medication_id, target) >= int(maximum):
            raise ValueError("PRN daily maximum has already been reached")
        instance = create_prn_instance(con, medication, target, uuid_factory)
        recorded = record_instance(con, instance["id"], "taken", when, note, uuid_factory)
        if request_id:
            con.execute(
                """INSERT INTO prn_requests(
                       request_id,medication_id,person_id,payload_hash,dose_instance_id,state
                   ) VALUES(?,?,?,?,?,'active')""",
                (
                    request_id,
                    medication_id,
                    medication["person_id"],
                    payload_hash,
                    instance["id"],
                ),
            )
        return recorded


__all__ = ["record_prn_intake"]
