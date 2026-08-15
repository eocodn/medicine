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
    lactation_status TEXT NOT NULL DEFAULT 'unknown',
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    note TEXT,
    product_name_snapshot TEXT,
    dosage_text_snapshot TEXT,
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
    "prn_max_per_day": "INTEGER",
    "prescription_days": "INTEGER",
    "long_term": "INTEGER NOT NULL DEFAULT 0",
    "stopped_at": "TEXT",
    "revision": "INTEGER NOT NULL DEFAULT 1",
    # SQLite cannot add a column with a CURRENT_TIMESTAMP default via ALTER
    # TABLE. Existing rows are backfilled below and an insert trigger supplies
    # the timestamp for legacy databases.
    "updated_at": "TEXT",
}

PERSON_COLUMNS = {
    "lactation_status": "TEXT NOT NULL DEFAULT 'unknown'",
}

DOSE_LOG_COLUMNS = {
    "dose_instance_id": "TEXT",
    "product_name_snapshot": "TEXT",
    "dosage_text_snapshot": "TEXT",
}

DOSE_INSTANCE_COLUMNS = {
    "product_name_snapshot": "TEXT",
    "ingredient_name_snapshot": "TEXT",
}

MEDICATION_REVISION_COLUMNS = {
    "medication_id": "TEXT",
    "revision": "INTEGER",
    "action": "TEXT",
    "snapshot_json": "TEXT",
    "assessment_json": "TEXT",
    "acknowledged": "INTEGER NOT NULL DEFAULT 0",
    "request_id": "TEXT",
    "payload_hash": "TEXT",
    "created_at": "TEXT",
}

MEDICATION_REQUEST_COLUMNS = {
    "request_id": "TEXT",
    "person_id": "TEXT",
    "payload_hash": "TEXT",
    "medication_id": "TEXT",
    "created_at": "TEXT",
}


def _existing_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _existing_columns(con, table)
    for name, definition in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")



def _migrate_occurrence_keys(con: sqlite3.Connection) -> None:
    """Convert time-derived dose identities to stable daily occurrence slots.

    Completed rows are historical facts, so only their internal identity changes.
    A two-phase rename avoids UNIQUE collisions when legacy time keys coexist with
    slot keys left by an earlier frequency-only regimen.
    """
    groups = con.execute(
        """SELECT DISTINCT medication_id,scheduled_date FROM dose_instances
           WHERE schedule_key LIKE 'time:%'"""
    ).fetchall()
    for medication_id, scheduled_date in groups:
        rows = con.execute(
            """SELECT id FROM dose_instances
               WHERE medication_id=? AND scheduled_date=? AND schedule_key NOT LIKE 'prn:%'
               ORDER BY CASE WHEN scheduled_time IS NULL THEN 1 ELSE 0 END,
                        scheduled_time,created_at,rowid""",
            (medication_id, scheduled_date),
        ).fetchall()
        for row in rows:
            con.execute(
                "UPDATE dose_instances SET schedule_key=? WHERE id=?",
                (f"migrate:{row[0]}", row[0]),
            )
        for index, row in enumerate(rows, 1):
            con.execute(
                "UPDATE dose_instances SET schedule_key=? WHERE id=?",
                (f"slot:{index}", row[0]),
            )


def ensure_personal_schema(con: sqlite3.Connection) -> None:
    con.executescript(BASE_SCHEMA)
    # Keep table creation separate from ALTER-based migration so databases
    # created by older app versions retain every existing row.
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dose_instances (
            id TEXT PRIMARY KEY,
            medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            scheduled_date TEXT NOT NULL,
            schedule_key TEXT NOT NULL,
            scheduled_time TEXT,
            slot_label TEXT,
            dose_text TEXT,
            product_name_snapshot TEXT,
            ingredient_name_snapshot TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id, scheduled_date, schedule_key)
        );

        CREATE TABLE IF NOT EXISTS medication_revisions (
            medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL,
            action TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            assessment_json TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,
            payload_hash TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id, revision)
        );

        CREATE TABLE IF NOT EXISTS medication_requests (
            request_id TEXT PRIMARY KEY,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
            payload_hash TEXT NOT NULL,
            medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    _add_missing_columns(con, "people", PERSON_COLUMNS)
    _add_missing_columns(con, "medications", MEDICATION_COLUMNS)
    _add_missing_columns(con, "dose_logs", DOSE_LOG_COLUMNS)
    _add_missing_columns(con, "dose_instances", DOSE_INSTANCE_COLUMNS)
    _add_missing_columns(con, "medication_revisions", MEDICATION_REVISION_COLUMNS)
    _add_missing_columns(con, "medication_requests", MEDICATION_REQUEST_COLUMNS)

    # ALTER TABLE cannot add a non-constant default. Backfill migrated rows,
    # while leaving snapshot fields nullable when their source row is absent.
    con.execute(
        """
        UPDATE medications
        SET updated_at=COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
            revision=COALESCE(revision, 1)
        WHERE updated_at IS NULL OR revision IS NULL
        """
    )
    con.execute(
        """
        UPDATE medications
        SET stopped_at=substr(COALESCE(updated_at, created_at), 1, 10)
        WHERE active=0 AND stopped_at IS NULL
        """
    )
    # Before long_term existed, an absent end date was the only representation
    # of an intentionally unbounded regimen. Preserve those existing records as
    # explicit long-term state instead of changing their schedule semantics.
    con.execute(
        """UPDATE medications SET long_term=1
           WHERE end_date IS NULL AND prescription_days IS NULL AND COALESCE(long_term,0)=0"""
    )
    _migrate_occurrence_keys(con)
    con.execute(
        """
        UPDATE people
        SET pregnancy_status='not_applicable', lactation_status='not_applicable'
        WHERE sex='male'
        """
    )
    con.execute(
        """
        UPDATE dose_instances
        SET product_name_snapshot=COALESCE(
                product_name_snapshot,
                (SELECT product_name FROM medications WHERE medications.id=dose_instances.medication_id)
            ),
            ingredient_name_snapshot=COALESCE(
                ingredient_name_snapshot,
                (SELECT ingredient_name FROM medications WHERE medications.id=dose_instances.medication_id)
            )
        WHERE product_name_snapshot IS NULL OR ingredient_name_snapshot IS NULL
        """
    )
    con.execute(
        """
        UPDATE dose_logs
        SET product_name_snapshot=COALESCE(
                product_name_snapshot,
                (SELECT product_name FROM medications WHERE medications.id=dose_logs.medication_id)
            ),
            dosage_text_snapshot=COALESCE(
                dosage_text_snapshot,
                (SELECT dosage_text FROM medications WHERE medications.id=dose_logs.medication_id)
            )
        WHERE product_name_snapshot IS NULL OR dosage_text_snapshot IS NULL
        """
    )
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_medications_person_active
            ON medications(person_id, active);
        CREATE INDEX IF NOT EXISTS idx_schedules_medication
            ON medication_schedules(medication_id);
        CREATE INDEX IF NOT EXISTS idx_logs_person_time
            ON dose_logs(person_id, occurred_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_instance_unique
            ON dose_logs(dose_instance_id) WHERE dose_instance_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_instances_person_date
            ON dose_instances(person_id, scheduled_date, scheduled_time);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_medication_revisions_medication_revision
            ON medication_revisions(medication_id, revision);
        CREATE INDEX IF NOT EXISTS idx_medication_revisions_request
            ON medication_revisions(request_id) WHERE request_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_medication_requests_person
            ON medication_requests(person_id);
        CREATE INDEX IF NOT EXISTS idx_medication_requests_medication
            ON medication_requests(medication_id);

        -- Legacy ALTER TABLE cannot provide a CURRENT_TIMESTAMP default.
        CREATE TRIGGER IF NOT EXISTS trg_medications_set_updated_at_on_insert
        AFTER INSERT ON medications
        WHEN NEW.updated_at IS NULL
        BEGIN
            UPDATE medications SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id;
        END;

        CREATE TRIGGER IF NOT EXISTS trg_medications_set_updated_at_on_update
        AFTER UPDATE ON medications
        WHEN NEW.updated_at IS OLD.updated_at
        BEGIN
            UPDATE medications SET updated_at=CURRENT_TIMESTAMP WHERE id=NEW.id;
        END;

        -- Revision rows are immutable during normal medication operations.
        -- Whole-profile privacy erasure is the sole explicit deletion path.
        CREATE TRIGGER IF NOT EXISTS trg_medication_revisions_append_only_update
        BEFORE UPDATE ON medication_revisions
        BEGIN
            SELECT RAISE(ABORT, 'medication_revisions is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_dose_instances_snapshots_immutable
        BEFORE UPDATE OF product_name_snapshot, ingredient_name_snapshot ON dose_instances
        WHEN NEW.product_name_snapshot IS NOT OLD.product_name_snapshot
          OR NEW.ingredient_name_snapshot IS NOT OLD.ingredient_name_snapshot
        BEGIN
            SELECT RAISE(ABORT, 'dose instance snapshots are immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_dose_logs_snapshots_immutable
        BEFORE UPDATE OF product_name_snapshot, dosage_text_snapshot ON dose_logs
        WHEN NEW.product_name_snapshot IS NOT OLD.product_name_snapshot
          OR NEW.dosage_text_snapshot IS NOT OLD.dosage_text_snapshot
        BEGIN
            SELECT RAISE(ABORT, 'dose log snapshots are immutable');
        END;
        """
    )
    con.execute("DROP TRIGGER IF EXISTS trg_medication_revisions_append_only_delete")
