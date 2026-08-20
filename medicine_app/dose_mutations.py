from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .planning import record_instance
from .prn import record_prn_intake
from .safety import APP_TIMEZONE

DOSE_STATUS_VALUES = {"taken", "skipped"}
MISSING = object()


def record_scheduled_dose(
    app: Any,
    instance_id: str,
    status: str,
    uuid_factory: Callable[[], str],
    occurred_at: str | None | object = MISSING,
    note: str | None | object = MISSING,
) -> dict:
    if status not in DOSE_STATUS_VALUES:
        raise ValueError("status must be taken or skipped")
    occurred_at_supplied = occurred_at is not MISSING
    note_supplied = note is not MISSING
    occurred_value = None if occurred_at is MISSING else occurred_at
    note_value = None if note is MISSING else note
    preserve_existing_same_state = not occurred_at_supplied and not note_supplied
    when = occurred_value or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    datetime.fromisoformat(when)
    # The instance row and its single dose-log row form one state transition.
    # Acquire the write transaction before reading either row so concurrent
    # record/cancel callers cannot interleave between observation and commit.
    with app._personal(write_lock=True) as con:
        return record_instance(
            con,
            instance_id,
            status,
            when,
            note_value,
            uuid_factory,
            preserve_existing_same_state=preserve_existing_same_state,
        )


def record_prn_dose(
    app: Any,
    medication_id: str,
    occurred_at: str | None,
    note: str | None,
    uuid_factory: Callable[[], str],
    *,
    request_id: str | None,
) -> dict:
    return record_prn_intake(
        app,
        medication_id,
        occurred_at,
        note,
        uuid_factory,
        request_id=request_id,
    )


__all__ = ["MISSING", "record_prn_dose", "record_scheduled_dose"]