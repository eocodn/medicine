//! Creation and migration of the encrypted application's personal SQLite DB.
//!
//! The schema is deliberately owned by the Rust core.  Android and local
//! development callers may open the same file, but only this module decides
//! when a schema transition is complete.  A schema marker is written last;
//! callers must still validate the core objects instead of trusting it alone.

mod validation;

use rusqlite::{Connection, Transaction, TransactionBehavior};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};
use std::time::Duration;

pub(crate) const SCHEMA_VERSION: i64 = 1;

#[derive(Debug)]
pub(crate) enum SchemaError {
    Io(io::Error),
    Sql(rusqlite::Error),
    CheckpointBusy,
    InvalidSchema(String),
}

impl Display for SchemaError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "personal database schema lock: {error}"),
            Self::Sql(error) => write!(formatter, "personal database schema: {error}"),
            Self::CheckpointBusy => formatter.write_str("personal database checkpoint is busy"),
            Self::InvalidSchema(detail) => {
                write!(formatter, "invalid personal database schema: {detail}")
            }
        }
    }
}

impl Error for SchemaError {}

impl From<rusqlite::Error> for SchemaError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sql(error)
    }
}

impl From<io::Error> for SchemaError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

struct SchemaLock(File);

impl SchemaLock {
    fn acquire(database: &Path) -> Result<Self, SchemaError> {
        let mut lock_path = database.as_os_str().to_os_string();
        lock_path.push(".schema.lock");
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(PathBuf::from(lock_path))?;
        // Keep a retained advisory lock file so concurrent app and CLI processes
        // serialize schema migration before opening their SQLite write boundary.
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if result != 0 {
            return Err(io::Error::last_os_error().into());
        }
        Ok(Self(file))
    }
}

impl Drop for SchemaLock {
    fn drop(&mut self) {
        // Unlock failure cannot be recovered here; closing the descriptor
        // immediately afterwards releases the advisory lock authoritatively.
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

/// Create or migrate a personal database in one immediate transaction.
pub(crate) fn initialize(path: &Path) -> Result<(), SchemaError> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    let _schema_lock = SchemaLock::acquire(path)?;
    let mut connection = Connection::open(path)?;
    connection.busy_timeout(Duration::from_secs(5))?;
    validation::reject_future_version(&connection)?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;
    ensure(&mut connection)
}

/// Apply the current schema to an already-open read-write connection.
pub(crate) fn ensure(connection: &mut Connection) -> Result<(), SchemaError> {
    validation::reject_future_version(connection)?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    create_tables(&transaction)?;
    add_legacy_columns(&transaction)?;
    backfill(&transaction)?;
    migrate_occurrence_keys(&transaction)?;
    install_indexes_and_triggers(&transaction)?;
    validation::validate_required_columns(&transaction)?;
    // user_version is an advisory marker.  It is deliberately the final
    // statement in the transaction and ensure() always verifies core tables.
    transaction.pragma_update(None, "user_version", SCHEMA_VERSION)?;
    transaction.commit()?;
    Ok(())
}

/// Checkpoint the WAL without treating a writer lock as success.
pub(crate) fn checkpoint(path: &Path) -> Result<(), SchemaError> {
    let connection = Connection::open(path)?;
    // A checkpoint is a maintenance boundary, not a request that may wait for
    // an arbitrary writer.  SQLite reports a busy result immediately.
    connection.busy_timeout(Duration::ZERO)?;
    let busy: i64 =
        connection.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| row.get(0))?;
    if busy != 0 {
        return Err(SchemaError::CheckpointBusy);
    }
    Ok(())
}

fn create_tables(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    transaction.execute_batch(
        r#"
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

        CREATE TABLE IF NOT EXISTS prn_requests (
            request_id TEXT PRIMARY KEY,
            medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
            person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
            payload_hash TEXT NOT NULL,
            dose_instance_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('active','canceled')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        "#,
    )?;
    Ok(())
}

fn add_legacy_columns(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    add_missing_columns(
        transaction,
        "people",
        &[("lactation_status", "TEXT NOT NULL DEFAULT 'unknown'")],
    )?;
    add_missing_columns(
        transaction,
        "medications",
        &[
            ("catalog_item_seq", "TEXT"),
            ("manufacturer", "TEXT"),
            ("catalog_source", "TEXT"),
            ("dose_amount", "REAL"),
            ("dose_unit", "TEXT"),
            ("frequency_per_day", "INTEGER"),
            ("meal_relation", "TEXT"),
            ("administration_route", "TEXT"),
            ("as_needed", "INTEGER NOT NULL DEFAULT 0"),
            ("prn_max_per_day", "INTEGER"),
            ("prescription_days", "INTEGER"),
            ("long_term", "INTEGER NOT NULL DEFAULT 0"),
            ("stopped_at", "TEXT"),
            ("revision", "INTEGER NOT NULL DEFAULT 1"),
            ("updated_at", "TEXT"),
        ],
    )?;
    add_missing_columns(
        transaction,
        "dose_logs",
        &[
            ("dose_instance_id", "TEXT"),
            ("product_name_snapshot", "TEXT"),
            ("dosage_text_snapshot", "TEXT"),
        ],
    )?;
    add_missing_columns(
        transaction,
        "dose_instances",
        &[
            ("product_name_snapshot", "TEXT"),
            ("ingredient_name_snapshot", "TEXT"),
        ],
    )?;
    add_missing_columns(
        transaction,
        "medication_revisions",
        &[
            ("medication_id", "TEXT"),
            ("revision", "INTEGER"),
            ("action", "TEXT"),
            ("snapshot_json", "TEXT"),
            ("assessment_json", "TEXT"),
            ("acknowledged", "INTEGER NOT NULL DEFAULT 0"),
            ("request_id", "TEXT"),
            ("payload_hash", "TEXT"),
            ("created_at", "TEXT"),
        ],
    )?;
    add_missing_columns(
        transaction,
        "medication_requests",
        &[
            ("request_id", "TEXT"),
            ("person_id", "TEXT"),
            ("payload_hash", "TEXT"),
            ("medication_id", "TEXT"),
            ("created_at", "TEXT"),
        ],
    )?;
    Ok(())
}

fn add_missing_columns(
    transaction: &Transaction<'_>,
    table: &str,
    columns: &[(&str, &str)],
) -> Result<(), SchemaError> {
    let existing = {
        let mut statement = transaction.prepare(&format!("PRAGMA table_info({table})"))?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(1))?
            .collect::<Result<Vec<_>, _>>()?;
        rows
    };
    for (name, definition) in columns {
        if !existing.iter().any(|column| column == name) {
            transaction.execute(
                &format!("ALTER TABLE {table} ADD COLUMN {name} {definition}"),
                [],
            )?;
        }
    }
    Ok(())
}

fn backfill(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    transaction.execute_batch(
        r#"
        UPDATE medications
        SET updated_at=COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
            revision=COALESCE(revision, 1)
        WHERE updated_at IS NULL OR revision IS NULL;

        UPDATE medications
        SET stopped_at=substr(COALESCE(updated_at, created_at), 1, 10)
        WHERE active=0 AND stopped_at IS NULL;

        UPDATE medications SET long_term=1
        WHERE end_date IS NULL AND prescription_days IS NULL
          AND COALESCE(long_term,0)=0;

        UPDATE people
        SET pregnancy_status='not_applicable', lactation_status='not_applicable'
        WHERE sex='male';

        UPDATE dose_instances
        SET product_name_snapshot=COALESCE(
                product_name_snapshot,
                (SELECT product_name FROM medications
                 WHERE medications.id=dose_instances.medication_id)
            ),
            ingredient_name_snapshot=COALESCE(
                ingredient_name_snapshot,
                (SELECT ingredient_name FROM medications
                 WHERE medications.id=dose_instances.medication_id)
            )
        WHERE product_name_snapshot IS NULL OR ingredient_name_snapshot IS NULL;

        UPDATE dose_logs
        SET product_name_snapshot=COALESCE(
                product_name_snapshot,
                (SELECT product_name FROM medications
                 WHERE medications.id=dose_logs.medication_id)
            ),
            dosage_text_snapshot=COALESCE(
                dosage_text_snapshot,
                (SELECT dosage_text FROM medications
                 WHERE medications.id=dose_logs.medication_id)
            )
        WHERE product_name_snapshot IS NULL OR dosage_text_snapshot IS NULL;
        "#,
    )?;
    Ok(())
}

fn migrate_occurrence_keys(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    let groups = {
        let mut statement = transaction.prepare(
            "SELECT DISTINCT medication_id,scheduled_date FROM dose_instances
             WHERE schedule_key LIKE 'time:%'",
        )?;
        let rows = statement
            .query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
            })?
            .collect::<Result<Vec<_>, _>>()?;
        rows
    };
    for (medication_id, scheduled_date) in groups {
        let rows = {
            let mut statement = transaction.prepare(
                "SELECT id,rowid FROM dose_instances
                 WHERE medication_id=? AND scheduled_date=? AND schedule_key NOT LIKE 'prn:%'
                 ORDER BY CASE WHEN scheduled_time IS NULL THEN 1 ELSE 0 END,
                          scheduled_time,created_at,rowid",
            )?;
            let rows = statement
                .query_map((&medication_id, &scheduled_date), |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
                })?
                .collect::<Result<Vec<_>, _>>()?;
            rows
        };
        // First move every row away from both the old time keys and existing
        // slot keys.  The generated namespace is checked before each write so
        // even a hand-crafted legacy key cannot collide with this phase.
        for (id, rowid) in &rows {
            let mut temporary = format!("__occurrence_migration__:{rowid}");
            while transaction.query_row(
                "SELECT EXISTS(SELECT 1 FROM dose_instances WHERE schedule_key=? AND id<>?)",
                (&temporary, id),
                |row| row.get::<_, bool>(0),
            )? {
                temporary.push('_');
            }
            transaction.execute(
                "UPDATE dose_instances SET schedule_key=? WHERE id=?",
                (&temporary, id),
            )?;
        }
        for (index, (id, _)) in rows.iter().enumerate() {
            transaction.execute(
                "UPDATE dose_instances SET schedule_key=? WHERE id=?",
                (format!("slot:{}", index + 1), id),
            )?;
        }
    }
    Ok(())
}

fn install_indexes_and_triggers(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    transaction.execute_batch(
        r#"
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
        CREATE INDEX IF NOT EXISTS idx_prn_requests_person
            ON prn_requests(person_id);
        CREATE INDEX IF NOT EXISTS idx_prn_requests_instance
            ON prn_requests(dose_instance_id);

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

        DROP TRIGGER IF EXISTS trg_medication_revisions_append_only_delete;
        "#,
    )?;
    Ok(())
}
