mod common;

use medicine_core::{checkpoint_personal_db, initialize_personal_db};
use rusqlite::Connection;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Barrier};
use std::thread;

fn personal_db(label: &str) -> PathBuf {
    common::temp_sqlite_path(label)
}

fn object_exists(con: &Connection, kind: &str, name: &str) -> bool {
    con.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type=? AND name=?)",
        (kind, name),
        |row| row.get(0),
    )
    .expect("query sqlite object")
}

fn column_exists(con: &Connection, table: &str, column: &str) -> bool {
    let mut statement = con
        .prepare(&format!("PRAGMA table_info({table})"))
        .expect("inspect table columns");
    let exists = statement
        .query_map([], |row| row.get::<_, String>(1))
        .expect("read table columns")
        .filter_map(Result::ok)
        .any(|value| value == column);
    exists
}

fn run_schema(path: &Path) {
    initialize_personal_db(path).expect("initialize personal schema");
}

#[test]
fn empty_database_gets_complete_schema_and_repeated_init_is_idempotent() {
    let path = personal_db("schema-empty");
    run_schema(&path);

    let con = Connection::open(&path).expect("open initialized personal database");
    for table in [
        "people",
        "medications",
        "medication_schedules",
        "dose_logs",
        "dose_instances",
        "medication_revisions",
        "medication_requests",
        "prn_requests",
    ] {
        assert!(object_exists(&con, "table", table), "missing table {table}");
    }
    assert!(column_exists(&con, "people", "lactation_status"));
    assert!(column_exists(&con, "medications", "long_term"));
    assert!(column_exists(&con, "medications", "prn_max_per_day"));
    assert!(column_exists(&con, "dose_instances", "schedule_key"));
    assert!(column_exists(&con, "dose_logs", "dose_instance_id"));
    drop(con);

    // A second process startup must not duplicate objects or rewrite data.
    run_schema(&path);
    let con = Connection::open(&path).expect("reopen idempotent personal database");
    assert_eq!(
        con.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .expect("count tables"),
        8
    );
    fs::remove_file(path).ok();
}

#[test]
fn concurrent_initializers_serialize_on_one_schema_boundary() {
    let path = personal_db("schema-concurrent");
    let barrier = Arc::new(Barrier::new(8));
    let workers: Vec<_> = (0..8)
        .map(|_| {
            let path = path.clone();
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                initialize_personal_db(&path)
            })
        })
        .collect();
    for worker in workers {
        worker
            .join()
            .expect("initializer thread")
            .expect("initialize");
    }

    let con = Connection::open(&path).expect("open concurrently initialized database");
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("schema version"),
        1
    );
    assert!(object_exists(&con, "table", "prn_requests"));
    fs::remove_file(path).ok();
}

#[test]
fn incomplete_existing_schema_is_rejected_without_rewriting_it() {
    let path = personal_db("schema-incomplete");
    let con = Connection::open(&path).expect("create incomplete personal database");
    con.execute_batch(
        "CREATE TABLE people(
             id TEXT PRIMARY KEY,name TEXT NOT NULL,birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,pregnancy_status TEXT NOT NULL,notes TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         INSERT INTO people(id,name,birth_date,sex,pregnancy_status)
         VALUES('p','P','1990-01-01','male','not_applicable');",
    )
    .expect("create incomplete people table");
    drop(con);

    assert!(initialize_personal_db(&path).is_err());
    let con = Connection::open(&path).expect("reopen rejected incomplete database");
    assert!(!column_exists(&con, "people", "lactation_status"));
    assert!(!object_exists(&con, "table", "medications"));
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("schema marker"),
        0
    );
    fs::remove_file(path).ok();
}

#[test]
fn future_schema_version_is_rejected_without_downgrade() {
    let path = personal_db("schema-future-version");
    run_schema(&path);
    let con = Connection::open(&path).expect("open current personal database");
    con.pragma_update(None, "user_version", 99)
        .expect("mark future schema");
    drop(con);

    assert!(initialize_personal_db(&path).is_err());
    let con = Connection::open(&path).expect("reopen future personal database");
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("future schema marker"),
        99
    );
    fs::remove_file(path).ok();
}

#[test]
fn schema_indexes_and_immutability_triggers_are_installed() {
    let path = personal_db("schema-integrity");
    run_schema(&path);
    let con = Connection::open(&path).expect("open personal schema");
    for index in [
        "idx_medications_person_active",
        "idx_schedules_medication",
        "idx_logs_person_time",
        "idx_logs_instance_unique",
        "idx_instances_person_date",
        "idx_medication_revisions_medication_revision",
        "idx_medication_revisions_request",
        "idx_medication_requests_person",
        "idx_medication_requests_medication",
        "idx_prn_requests_person",
        "idx_prn_requests_instance",
    ] {
        assert!(object_exists(&con, "index", index), "missing index {index}");
    }
    for trigger in [
        "trg_medications_set_updated_at_on_insert",
        "trg_medications_set_updated_at_on_update",
        "trg_medication_revisions_append_only_update",
        "trg_dose_instances_snapshots_immutable",
        "trg_dose_logs_snapshots_immutable",
    ] {
        assert!(
            object_exists(&con, "trigger", trigger),
            "missing trigger {trigger}"
        );
    }

    con.execute(
        "INSERT INTO people(id,name,birth_date,sex,pregnancy_status) VALUES('p','P','1990-01-01','male','not_applicable')",
        [],
    )
    .expect("insert trigger fixture person");
    con.execute(
        "INSERT INTO medications(id,person_id,product_name) VALUES('m','p','Medicine')",
        [],
    )
    .expect("insert trigger fixture medication");
    con.execute(
        "INSERT INTO medication_revisions(medication_id,revision,action,snapshot_json) VALUES('m',1,'create','{}')",
        [],
    )
    .expect("insert revision fixture");
    assert!(con
        .execute(
            "UPDATE medication_revisions SET action='tampered' WHERE medication_id='m'",
            []
        )
        .is_err());
    con.execute(
        "INSERT INTO dose_instances(id,medication_id,person_id,scheduled_date,schedule_key,product_name_snapshot,ingredient_name_snapshot) VALUES('i','m','p','2026-08-22','slot:1','Medicine','Ingredient')",
        [],
    )
    .expect("insert dose snapshot fixture");
    assert!(con
        .execute(
            "UPDATE dose_instances SET product_name_snapshot='tampered' WHERE id='i'",
            []
        )
        .is_err());
    fs::remove_file(path).ok();
}

#[test]
fn checkpoint_truncate_succeeds_and_busy_is_reported_as_failure() {
    let path = personal_db("schema-checkpoint");
    run_schema(&path);
    let writer = Connection::open(&path).expect("open checkpoint writer");
    writer
        .execute(
            "INSERT INTO people(id,name,birth_date,sex,pregnancy_status) VALUES('p','P','1990-01-01','male','not_applicable')",
            [],
        )
        .expect("write WAL content");
    assert!(checkpoint_personal_db(&path).is_ok());

    writer
        .execute_batch("BEGIN IMMEDIATE")
        .expect("hold writer lock");
    assert!(
        checkpoint_personal_db(&path).is_err(),
        "busy checkpoint must fail"
    );
    writer
        .execute_batch("ROLLBACK")
        .expect("release writer lock");
    assert!(checkpoint_personal_db(&path).is_ok());
    fs::remove_file(path).ok();
}
