from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .planning import create_prn_instance, medication_applies_on, prn_taken_count, record_instance
from .safety import APP_TIMEZONE


def record_prn_intake(
    app: Any,
    medication_id: str,
    occurred_at: str | None,
    note: str | None,
    uuid_factory: Callable[[], str],
) -> dict:
    when = occurred_at or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    parsed = datetime.fromisoformat(when)
    target = parsed.astimezone(APP_TIMEZONE).date() if parsed.tzinfo else parsed.date()
    with app._personal(write_lock=True) as con:
        medication = app._get_medication_from_connection(con, medication_id)
        if not medication_applies_on(medication, target):
            raise ValueError("PRN medication is not active on the intake date")
        if not medication.get("as_needed"):
            raise ValueError("medication is not PRN/as_needed")
        maximum = medication.get("prn_max_per_day")
        if maximum is not None and prn_taken_count(con, medication_id, target) >= int(maximum):
            raise ValueError("PRN daily maximum has already been reached")
        instance = create_prn_instance(con, medication, target, uuid_factory)
        return record_instance(con, instance["id"], "taken", when, note, uuid_factory)


__all__ = ["record_prn_intake"]
