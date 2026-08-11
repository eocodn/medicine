from __future__ import annotations

import sqlite3
from datetime import date

from .safety import age_years


SEX_VALUES = {"female", "male", "other", "unknown"}
PREGNANCY_VALUES = {"pregnant", "not_pregnant", "unknown", "not_applicable"}
LACTATION_VALUES = {"breastfeeding", "not_breastfeeding", "unknown", "not_applicable"}


def _parse_birth_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("birth_date must be YYYY-MM-DD") from exc


def _normalize_reproductive_status(
    sex: str,
    pregnancy_status: str,
    lactation_status: str,
) -> tuple[str, str]:
    if sex not in SEX_VALUES:
        raise ValueError(f"invalid sex: {sex}")
    if pregnancy_status not in PREGNANCY_VALUES:
        raise ValueError(f"invalid pregnancy_status: {pregnancy_status}")
    if lactation_status not in LACTATION_VALUES:
        raise ValueError(f"invalid lactation_status: {lactation_status}")
    if sex == "male":
        return "not_applicable", "not_applicable"
    return pregnancy_status, lactation_status


def person_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["age"] = age_years(data["birth_date"])
    return data


def create_person_record(
    con: sqlite3.Connection,
    person_id: str,
    name: str,
    birth_date: str,
    sex: str = "unknown",
    pregnancy_status: str = "unknown",
    lactation_status: str = "unknown",
    notes: str | None = None,
) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    _parse_birth_date(birth_date)
    pregnancy_status, lactation_status = _normalize_reproductive_status(
        sex, pregnancy_status, lactation_status
    )
    con.execute(
        """INSERT INTO people(
            id,name,birth_date,sex,pregnancy_status,lactation_status,notes
        ) VALUES(?,?,?,?,?,?,?)""",
        (person_id, name, birth_date, sex, pregnancy_status, lactation_status, notes),
    )
    row = con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
    return person_dict(row)


def update_person_record(
    con: sqlite3.Connection,
    person_id: str,
    name: str,
    birth_date: str,
    sex: str,
    pregnancy_status: str,
    lactation_status: str,
    notes: str | None = None,
) -> dict:
    if con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone() is None:
        raise KeyError("person not found")
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    _parse_birth_date(birth_date)
    pregnancy_status, lactation_status = _normalize_reproductive_status(
        sex, pregnancy_status, lactation_status
    )
    con.execute(
        """UPDATE people
        SET name=?,birth_date=?,sex=?,pregnancy_status=?,lactation_status=?,notes=?
        WHERE id=?""",
        (name, birth_date, sex, pregnancy_status, lactation_status, notes, person_id),
    )
    return person_dict(con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone())


def delete_person_record(con: sqlite3.Connection, person_id: str) -> dict:
    if con.execute("SELECT 1 FROM people WHERE id=?", (person_id,)).fetchone() is None:
        raise KeyError("person not found")
    # Medication revisions are immutable during normal use, but a deliberate
    # whole-profile erasure must remove every personal record atomically.
    con.execute("DELETE FROM dose_logs WHERE person_id=?", (person_id,))
    con.execute("DELETE FROM dose_instances WHERE person_id=?", (person_id,))
    con.execute("DELETE FROM medication_requests WHERE person_id=?", (person_id,))
    con.execute(
        "DELETE FROM medication_schedules WHERE medication_id IN "
        "(SELECT id FROM medications WHERE person_id=?)",
        (person_id,),
    )
    con.execute(
        "DELETE FROM medication_revisions WHERE medication_id IN "
        "(SELECT id FROM medications WHERE person_id=?)",
        (person_id,),
    )
    con.execute("DELETE FROM medications WHERE person_id=?", (person_id,))
    con.execute("DELETE FROM people WHERE id=?", (person_id,))
    return {"id": person_id, "deleted": True}


__all__ = [
    "create_person_record",
    "delete_person_record",
    "person_dict",
    "update_person_record",
]
