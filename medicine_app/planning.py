from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Callable

from .safety import APP_TIMEZONE


def clock_sort_key(value: str) -> tuple[int, int]:
    """Parse a validated clock value numerically, including legacy H:MM rows."""
    hour_text, minute_text = value.split(":", 1)
    hour, minute = int(hour_text), int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("schedule time must be HH:MM")
    return hour, minute


def medication_course_progress(medication: dict, target: date) -> dict | None:
    """Return calendar progress; active remaining days exclude the target day."""
    if not medication.get("start_date") or not medication.get("end_date"):
        return None
    start = date.fromisoformat(medication["start_date"])
    end = date.fromisoformat(medication["end_date"])
    total_days = (end - start).days + 1
    if total_days <= 0:
        raise ValueError("medication end_date must be on or after start_date")
    if target < start:
        status, current_day, remaining_days, progress_percent = "upcoming", 0, total_days, 0
    elif target > end:
        status, current_day, remaining_days, progress_percent = "completed", total_days, 0, 100
    else:
        current_day = (target - start).days + 1
        remaining_days = (end - target).days
        status = "active"
        progress_percent = round(current_day * 100 / total_days)
    return {
        "status": status,
        "total_days": total_days,
        "current_day": current_day,
        "remaining_days": remaining_days,
        "progress_percent": progress_percent,
    }


def sort_medications_by_time(medications: list[dict], target: date) -> list[dict]:
    """Annotate courses and sort explicit daily times before unscheduled medication."""
    annotated: list[dict] = []
    for medication in medications:
        item = dict(medication)
        item["course_progress"] = medication_course_progress(item, target)
        annotated.append(item)

    def key(item: dict) -> tuple[int, str]:
        times = [schedule["time_of_day"] for schedule in item.get("schedules") or []]
        if not times:
            return 1, ""
        hour, minute = min(clock_sort_key(value) for value in times)
        return 0, f"{hour:02d}:{minute:02d}"

    # Python's stable sort preserves the database list order for equal times.
    return sorted(annotated, key=key)


def medication_applies_on(medication: dict, target: date) -> bool:
    if not medication.get("active"):
        return False
    start = date.fromisoformat(medication["start_date"]) if medication.get("start_date") else None
    end = date.fromisoformat(medication["end_date"]) if medication.get("end_date") else None
    if start and target < start:
        return False
    if end and target > end:
        return False
    return True


def _desired_for_medication(medication: dict, target: date) -> list[dict]:
    dose_text = medication.get("dosage_text")
    schedules = sorted(
        medication.get("schedules") or [],
        key=lambda schedule: clock_sort_key(schedule["time_of_day"]),
    )
    if schedules:
        return [
            {
                "medication_id": medication["id"],
                "scheduled_date": target.isoformat(),
                "schedule_key": f"slot:{index}",
                "scheduled_time": schedule["time_of_day"],
                "slot_label": None,
                "dose_text": schedule.get("dose_text") or dose_text,
            }
            for index, schedule in enumerate(schedules, 1)
        ]

    frequency = medication.get("frequency_per_day")
    if frequency:
        return [
            {
                "medication_id": medication["id"],
                "scheduled_date": target.isoformat(),
                "schedule_key": f"slot:{index}",
                "scheduled_time": None,
                "slot_label": f"{index}회차",
                "dose_text": dose_text,
            }
            for index in range(1, int(frequency) + 1)
        ]
    return []


def materialize_daily_plan(
    con: sqlite3.Connection,
    person_id: str,
    medications: list[dict],
    target: date,
    uuid_factory: Callable[[], str],
) -> dict:
    applicable = [med for med in medications if medication_applies_on(med, target)]
    prn = [med for med in applicable if med.get("as_needed")]
    scheduled = [med for med in applicable if not med.get("as_needed")]
    unscheduled = [med for med in scheduled if not med.get("schedules") and not med.get("frequency_per_day")]

    desired = [item for med in scheduled for item in _desired_for_medication(med, target)]
    desired_keys = {(item["medication_id"], item["schedule_key"]) for item in desired}

    existing = con.execute(
        """
        SELECT id, medication_id, schedule_key, status, scheduled_time, slot_label, dose_text
        FROM dose_instances WHERE person_id=? AND scheduled_date=?
        """,
        (person_id, target.isoformat()),
    ).fetchall()
    existing_by_key = {
        (row["medication_id"], row["schedule_key"]): row
        for row in existing
    }
    for row in existing:
        key = (row["medication_id"], row["schedule_key"])
        if row["status"] == "planned" and key not in desired_keys:
            con.execute("DELETE FROM dose_instances WHERE id=?", (row["id"],))

    medication_by_id = {med["id"]: med for med in scheduled}
    for item in desired:
        key = (item["medication_id"], item["schedule_key"])
        current = existing_by_key.get(key)
        if current is None:
            medication = medication_by_id[item["medication_id"]]
            con.execute(
                """
                INSERT OR IGNORE INTO dose_instances(
                    id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
                    slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?, 'planned')
                """,
                (
                    uuid_factory(), item["medication_id"], person_id, item["scheduled_date"],
                    item["schedule_key"], item["scheduled_time"], item["slot_label"], item["dose_text"],
                    medication["product_name"], medication.get("ingredient_name"),
                ),
            )
            continue
        if current["status"] != "planned":
            continue
        if (
            current["scheduled_time"] == item["scheduled_time"]
            and current["slot_label"] == item["slot_label"]
            and current["dose_text"] == item["dose_text"]
        ):
            continue
        con.execute(
            """
            UPDATE dose_instances
            SET scheduled_time=?, slot_label=?, dose_text=?
            WHERE id=? AND status='planned'
            """,
            (item["scheduled_time"], item["slot_label"], item["dose_text"], current["id"]),
        )

    rows = list(con.execute(
        """
        SELECT i.*,
               COALESCE(i.product_name_snapshot,m.product_name) AS product_name,
               COALESCE(i.ingredient_name_snapshot,m.ingredient_name) AS ingredient_name,
               m.meal_relation, m.administration_route
        FROM dose_instances i JOIN medications m ON m.id=i.medication_id
        WHERE i.person_id=? AND i.scheduled_date=? AND i.schedule_key NOT LIKE 'prn:%'
        ORDER BY CASE WHEN i.scheduled_time IS NULL THEN 1 ELSE 0 END,
                 i.scheduled_time, i.schedule_key, i.rowid
        """,
        (person_id, target.isoformat()),
    ).fetchall())
    rows.sort(key=lambda row: (
        (0, *clock_sort_key(row["scheduled_time"]))
        if row["scheduled_time"] is not None
        else (1, int(row["schedule_key"].split(":", 1)[1]), 0)
    ))
    return {
        "date": target.isoformat(),
        "doses": [dict(row) for row in rows],
        "prn_medications": prn,
        "unscheduled_medications": unscheduled,
        "summary": {
            "planned": sum(1 for row in rows if row["status"] == "planned"),
            "taken": sum(1 for row in rows if row["status"] == "taken"),
            "skipped": sum(1 for row in rows if row["status"] == "skipped"),
        },
    }


def record_instance(
    con: sqlite3.Connection,
    instance_id: str,
    status: str,
    occurred_at: str,
    note: str | None,
    uuid_factory: Callable[[], str],
    *,
    preserve_existing_same_state: bool = False,
) -> dict:
    row = con.execute("SELECT * FROM dose_instances WHERE id=?", (instance_id,)).fetchone()
    if row is None:
        raise KeyError("dose instance not found")

    existing_log = con.execute(
        "SELECT id,status,occurred_at,note FROM dose_logs WHERE dose_instance_id=?", (instance_id,)
    ).fetchone()
    if preserve_existing_same_state and row["status"] == status and existing_log is not None:
        return dict(row)

    con.execute(
        "UPDATE dose_instances SET status=?, completed_at=? WHERE id=?",
        (status, occurred_at, instance_id),
    )
    if existing_log is None:
        con.execute(
            """
            INSERT INTO dose_logs(
                id,medication_id,person_id,status,occurred_at,note,dose_instance_id,
                product_name_snapshot,dosage_text_snapshot
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                uuid_factory(), row["medication_id"], row["person_id"], status, occurred_at,
                note, instance_id, row["product_name_snapshot"], row["dose_text"],
            ),
        )
    else:
        con.execute(
            "UPDATE dose_logs SET status=?, occurred_at=?, note=? WHERE id=?",
            (status, occurred_at, note, existing_log["id"]),
        )
    updated = con.execute("SELECT * FROM dose_instances WHERE id=?", (instance_id,)).fetchone()
    return dict(updated)


def cancel_instance_completion(con: sqlite3.Connection, instance_id: str) -> dict:
    row = con.execute("SELECT * FROM dose_instances WHERE id=?", (instance_id,)).fetchone()
    if row is None:
        raise KeyError("dose instance not found")
    con.execute("DELETE FROM dose_logs WHERE dose_instance_id=?", (instance_id,))
    if str(row["schedule_key"]).startswith("prn:"):
        # A PRN instance exists only because an actual intake was recorded. Undoing
        # that intake removes the ad-hoc occurrence instead of leaving an invisible
        # planned dose which can never appear in a fixed daily schedule.
        snapshot = dict(row)
        con.execute("DELETE FROM dose_instances WHERE id=?", (instance_id,))
        snapshot.update(status="canceled", completed_at=None, deleted=True)
        return snapshot
    con.execute(
        "UPDATE dose_instances SET status='planned', completed_at=NULL WHERE id=?",
        (instance_id,),
    )
    updated = con.execute("SELECT * FROM dose_instances WHERE id=?", (instance_id,)).fetchone()
    return dict(updated)


def create_prn_instance(
    con: sqlite3.Connection,
    medication: dict,
    occurred_date: date,
    uuid_factory: Callable[[], str],
) -> dict:
    if not medication.get("as_needed"):
        raise ValueError("medication is not PRN/as_needed")
    instance_id = uuid_factory()
    con.execute(
        """INSERT INTO dose_instances(
               id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
               slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status
           ) VALUES(?,?,?,?,?,NULL,?,?,?,?, 'planned')""",
        (
            instance_id,
            medication["id"],
            medication["person_id"],
            occurred_date.isoformat(),
            f"prn:{instance_id}",
            "필요시",
            medication.get("dosage_text"),
            medication.get("product_name"),
            medication.get("ingredient_name"),
        ),
    )
    return dict(con.execute("SELECT * FROM dose_instances WHERE id=?", (instance_id,)).fetchone())


def prn_taken_count(con: sqlite3.Connection, medication_id: str, target: date) -> int:
    rows = con.execute(
        """SELECT l.occurred_at FROM dose_logs l
           JOIN dose_instances i ON i.id=l.dose_instance_id
           WHERE l.medication_id=? AND l.status='taken' AND i.schedule_key LIKE 'prn:%'""",
        (medication_id,),
    ).fetchall()
    count = 0
    for row in rows:
        try:
            occurred = datetime.fromisoformat(str(row[0]))
            occurred_date = (
                occurred.astimezone(APP_TIMEZONE).date() if occurred.tzinfo else occurred.date()
            )
            if occurred_date == target:
                count += 1
        except ValueError:
            continue
    return count
