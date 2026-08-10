from __future__ import annotations

import sqlite3


BASE_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT NOT NULL,
    sex TEXT NOT NULL,
    pregnancy_status TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    product_code TEXT,
    product_name TEXT NOT NULL,
    ingredient_code TEXT,
    ingredient_name TEXT,
    dosage_text TEXT,
    start_date TEXT,
    end_date TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'dur_search',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medication_schedules (
    id TEXT PRIMARY KEY,
    medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
    time_of_day TEXT NOT NULL,
    dose_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dose_logs (
    id TEXT PRIMARY KEY,
    medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


MEDICATION_COLUMNS = {
    "catalog_item_seq": "TEXT",
    "manufacturer": "TEXT",
    "catalog_source": "TEXT",
    "dose_amount": "REAL",
    "dose_unit": "TEXT",
    "frequency_per_day": "INTEGER",
    "meal_relation": "TEXT",
    "administration_route": "TEXT",
    "as_needed": "INTEGER NOT NULL DEFAULT 0",
    "prescription_days": "INTEGER",
}

DOSE_LOG_COLUMNS = {
    "dose_instance_id": "TEXT",
}


def _existing_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _existing_columns(con, table)
    for name, definition in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_personal_schema(con: sqlite3.Connection) -> None:
    con.executescript(BASE_SCHEMA)
    _add_missing_columns(con, "medications", MEDICATION_COLUMNS)
    _add_missing_columns(con, "dose_logs", DOSE_LOG_COLUMNS)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dose_instances (
            id TEXT PRIMARY KEY,
            medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            scheduled_date TEXT NOT NULL,
            schedule_key TEXT NOT NULL,
            scheduled_time TEXT,
            slot_label TEXT,
            dose_text TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id, scheduled_date, schedule_key)
        );

        CREATE INDEX IF NOT EXISTS idx_medications_person_active ON medications(person_id, active);
        CREATE INDEX IF NOT EXISTS idx_schedules_medication ON medication_schedules(medication_id);
        CREATE INDEX IF NOT EXISTS idx_logs_person_time ON dose_logs(person_id, occurred_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_instance_unique
            ON dose_logs(dose_instance_id) WHERE dose_instance_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_instances_person_date
            ON dose_instances(person_id, scheduled_date, scheduled_time);
        """
    )
