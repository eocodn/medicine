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
fn failed_legacy_migration_rolls_back_all_schema_changes() {
    let path = personal_db("schema-rollback");
    let con = Connection::open(&path).expect("create incompatible legacy database");
    con.execute_batch("CREATE TABLE people(id TEXT PRIMARY KEY);")
        .expect("create incompatible people table");
    drop(con);

    assert!(initialize_personal_db(&path).is_err());
    let con = Connection::open(&path).expect("reopen failed migration database");
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
fn malformed_core_schema_is_not_marked_current() {
    let path = personal_db("schema-malformed-core");
    let con = Connection::open(&path).expect("create malformed legacy database");
    con.execute_batch(
        "CREATE TABLE people(
             id TEXT PRIMARY KEY,name TEXT NOT NULL,birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,pregnancy_status TEXT NOT NULL,
             lactation_status TEXT NOT NULL DEFAULT 'unknown',
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         INSERT INTO people(id,name,birth_date,sex,pregnancy_status)
         VALUES('p','P','1990-01-01','male','not_applicable');",
    )
    .expect("create malformed people table without notes");
    drop(con);

    assert!(initialize_personal_db(&path).is_err());
    let con = Connection::open(&path).expect("reopen rejected malformed database");
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("schema marker"),
        0
    );
    assert_eq!(
        con.query_row("SELECT COUNT(*) FROM people", [], |row| row
            .get::<_, i64>(0))
            .expect("preserved person"),
        1
    );
    assert!(!object_exists(&con, "table", "medications"));
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
fn legacy_four_table_rows_are_preserved_and_new_state_is_backfilled() {
    let path = personal_db("schema-legacy");
    let con = Connection::open(&path).expect("create legacy personal database");
    con.execute_batch(
        "CREATE TABLE people(
             id TEXT PRIMARY KEY,name TEXT NOT NULL,birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,pregnancy_status TEXT NOT NULL,notes TEXT,
             created_at TEXT NOT NULL
         );
         CREATE TABLE medications(
             id TEXT PRIMARY KEY,person_id TEXT NOT NULL,product_code TEXT,
             product_name TEXT NOT NULL,ingredient_code TEXT,ingredient_name TEXT,
             dosage_text TEXT,start_date TEXT,end_date TEXT,active INTEGER NOT NULL,
             source TEXT NOT NULL,created_at TEXT NOT NULL
         );
         CREATE TABLE medication_schedules(
             id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,time_of_day TEXT NOT NULL,
             dose_text TEXT,created_at TEXT NOT NULL
         );
         CREATE TABLE dose_logs(
             id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,
             status TEXT NOT NULL,occurred_at TEXT NOT NULL,note TEXT,created_at TEXT NOT NULL
         );
         INSERT INTO people(id,name,birth_date,sex,pregnancy_status,notes,created_at)
         VALUES('p-legacy','Legacy','1980-01-02','male','pregnant','keep me','2025-03-04 11:00:00');
         INSERT INTO medications(
             id,person_id,product_code,product_name,ingredient_code,ingredient_name,
             dosage_text,start_date,end_date,active,source,created_at
         ) VALUES(
             'm-legacy','p-legacy','P-OLD','Old medicine','I-OLD','Old ingredient',
             '1정','2025-03-01',NULL,0,'legacy','2025-03-04 11:00:00'
         );
         INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text,created_at)
         VALUES('s-legacy','m-legacy','08:00','1정','2025-03-04 11:00:00');
         INSERT INTO dose_logs(id,medication_id,person_id,status,occurred_at,note,created_at)
         VALUES('l-legacy','m-legacy','p-legacy','taken','2025-03-04T08:00:00+09:00','keep log','2025-03-04 11:00:00');
         CREATE TABLE dose_instances(
             id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,
             scheduled_date TEXT NOT NULL,schedule_key TEXT NOT NULL,scheduled_time TEXT,
             slot_label TEXT,dose_text TEXT,status TEXT NOT NULL,completed_at TEXT,
             created_at TEXT NOT NULL,
             UNIQUE(medication_id,scheduled_date,schedule_key)
         );
         INSERT INTO dose_instances(
             id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
             slot_label,dose_text,status,completed_at,created_at
         ) VALUES
             ('i-slot','m-legacy','p-legacy','2025-03-04','slot:1','07:00','old','1정','planned',NULL,'2025-03-04 07:00:00'),
             ('i-time-1','m-legacy','p-legacy','2025-03-04','time:08:00','08:00','08','1정','taken','2025-03-04T08:00:00+09:00','2025-03-04 08:00:00'),
             ('i-time-2','m-legacy','p-legacy','2025-03-04','time:12:00','12:00','12','1정','completed','2025-03-04T12:00:00+09:00','2025-03-04 12:00:00');",
    )
    .expect("populate legacy personal database");
    drop(con);

    run_schema(&path);
    let con = Connection::open(&path).expect("open migrated personal database");
    assert_eq!(
        con.query_row("SELECT name FROM people WHERE id='p-legacy'", [], |row| row
            .get::<_, String>(0))
            .expect("preserved person"),
        "Legacy"
    );
    assert_eq!(
        con.query_row("SELECT COUNT(*) FROM medications", [], |row| row
            .get::<_, i64>(0))
            .expect("preserved medication count"),
        1
    );
    assert_eq!(
        con.query_row("SELECT COUNT(*) FROM medication_schedules", [], |row| row
            .get::<_, i64>(
            0
        ))
        .expect("preserved schedule count"),
        1
    );
    assert_eq!(
        con.query_row(
            "SELECT note FROM dose_logs WHERE id='l-legacy'",
            [],
            |row| row.get::<_, String>(0)
        )
        .expect("preserved dose log"),
        "keep log"
    );

    let person_state: (String, String) = con
        .query_row(
            "SELECT pregnancy_status,lactation_status FROM people WHERE id='p-legacy'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .expect("backfilled male profile state");
    assert_eq!(
        person_state,
        ("not_applicable".into(), "not_applicable".into())
    );

    let medication_state: (i64, Option<i64>, i64, String) = con
        .query_row(
            "SELECT as_needed,prn_max_per_day,long_term,stopped_at FROM medications WHERE id='m-legacy'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .expect("backfilled medication state");
    assert_eq!(medication_state, (0, None, 1, "2025-03-04".into()));

    let snapshots: (String, String, String, String) = con
        .query_row(
            "SELECT product_name_snapshot,ingredient_name_snapshot,
                    (SELECT product_name_snapshot FROM dose_logs WHERE id='l-legacy'),
                    (SELECT dosage_text_snapshot FROM dose_logs WHERE id='l-legacy')
             FROM dose_instances WHERE id='i-time-2'",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .expect("backfilled immutable snapshots");
    assert_eq!(
        snapshots,
        (
            "Old medicine".into(),
            "Old ingredient".into(),
            "Old medicine".into(),
            "1정".into()
        )
    );

    let occurrence_keys: Vec<(String, String, String)> = con
        .prepare("SELECT id,schedule_key,status FROM dose_instances ORDER BY schedule_key")
        .expect("prepare occurrence query")
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .expect("read occurrence keys")
        .map(|row| row.expect("occurrence row"))
        .collect();
    assert_eq!(
        occurrence_keys,
        vec![
            ("i-slot".into(), "slot:1".into(), "planned".into()),
            ("i-time-1".into(), "slot:2".into(), "taken".into()),
            ("i-time-2".into(), "slot:3".into(), "completed".into()),
        ]
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
fn obsolete_revision_delete_trigger_is_removed_for_profile_erasure() {
    let path = personal_db("schema-delete-trigger");
    run_schema(&path);
    let con = Connection::open(&path).expect("open personal schema");
    con.execute_batch(
        "CREATE TRIGGER trg_medication_revisions_append_only_delete
         BEFORE DELETE ON medication_revisions
         BEGIN
             SELECT RAISE(ABORT, 'medication_revisions is append-only');
         END;",
    )
    .expect("install obsolete legacy trigger");
    drop(con);

    run_schema(&path);
    let con = Connection::open(&path).expect("reopen migrated personal schema");
    assert!(!object_exists(
        &con,
        "trigger",
        "trg_medication_revisions_append_only_delete"
    ));
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
