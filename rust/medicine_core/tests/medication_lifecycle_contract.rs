mod common;

use medicine_core::MedicineEngine;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn temp_medication_db() -> PathBuf {
    let path = common::temp_sqlite_path("medication-lifecycle");
    let con = Connection::open(&path).expect("create medication fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE people(id TEXT PRIMARY KEY);
         CREATE TABLE medications(
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
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             catalog_item_seq TEXT,
             manufacturer TEXT,
             catalog_source TEXT,
             dose_amount REAL,
             dose_unit TEXT,
             frequency_per_day INTEGER,
             meal_relation TEXT,
             administration_route TEXT,
             as_needed INTEGER NOT NULL DEFAULT 0,
             prn_max_per_day INTEGER,
             prescription_days INTEGER,
             long_term INTEGER NOT NULL DEFAULT 0,
             stopped_at TEXT
         );
         CREATE TABLE medication_schedules(
             id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
             time_of_day TEXT NOT NULL,
             dose_text TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         CREATE TABLE medication_revisions(
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
         CREATE TABLE dose_instances(
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
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         INSERT INTO people(id) VALUES('p1');
         INSERT INTO medications(
             id,person_id,product_code,product_name,ingredient_name,dosage_text,start_date,
             active,revision,catalog_item_seq,catalog_source,frequency_per_day,
             meal_relation,administration_route,long_term
         ) VALUES(
             'm1','p1','MFDS-A','약A','성분A','1정','2026-08-01',
             1,2,'MFDS-A','canonical',1,'after_meal','oral',1
         );
         INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
             VALUES('s1','m1','8:00','1정');
         INSERT INTO dose_instances(
             id,medication_id,person_id,scheduled_date,schedule_key,status
         ) VALUES
             ('past-planned','m1','p1','2000-01-01','slot:1','planned'),
             ('future-planned','m1','p1','2099-01-01','slot:1','planned'),
             ('future-taken','m1','p1','2099-01-01','slot:2','taken');",
    )
    .expect("create medication schema and fixtures");

    let create_snapshot = json!({
        "id": "m1",
        "person_id": "p1",
        "product_name": "약A",
        "active": true,
        "revision": 1,
        "schedules": [{"time_of_day": "8:00", "dose_text": "1정"}]
    });
    let update_snapshot = json!({
        "id": "m1",
        "person_id": "p1",
        "product_name": "약A",
        "active": true,
        "revision": 2,
        "schedules": [{"time_of_day": "8:00", "dose_text": "1정"}]
    });
    con.execute(
        "INSERT INTO medication_revisions(
             medication_id,revision,action,snapshot_json,assessment_json,acknowledged,request_id,payload_hash
         ) VALUES(?,?,?,?,?,?,?,?)",
        params![
            "m1",
            1,
            "create",
            create_snapshot.to_string(),
            json!({"marker": "create"}).to_string(),
            1,
            "create-request",
            "create-hash",
        ],
    )
    .expect("insert create revision");
    con.execute(
        "INSERT INTO medication_revisions(
             medication_id,revision,action,snapshot_json,assessment_json,acknowledged,request_id,payload_hash
         ) VALUES(?,?,?,?,?,?,?,?)",
        params![
            "m1",
            2,
            "update",
            update_snapshot.to_string(),
            json!({"marker": "current"}).to_string(),
            0,
            Option::<String>::None,
            "update-hash",
        ],
    )
    .expect("insert update revision");
    drop(con);
    path
}

fn response(engine: &MedicineEngine, method: &str, path: &str) -> Value {
    serde_json::from_str(&engine.request(method, path, "")).expect("decode medication response")
}

#[test]
fn medication_history_preserves_revision_json_contract() {
    let personal = temp_medication_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    let path = "/api/medications/m1/history";
    assert!(engine.handles_request("GET", path));

    let history = response(&engine, "GET", path);
    assert_eq!(history["status"], 200);
    let rows = history["body"].as_array().expect("history array");
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["revision"], 1);
    assert_eq!(rows[0]["action"], "create");
    assert_eq!(rows[0]["acknowledged"], true);
    assert_eq!(rows[0]["request_id"], "create-request");
    assert_eq!(rows[0]["payload_hash"], "create-hash");
    assert_eq!(rows[0]["snapshot"]["active"], true);
    assert_eq!(rows[0]["assessment"]["marker"], "create");
    assert!(rows[0].get("snapshot_json").is_none());
    assert!(rows[0].get("assessment_json").is_none());
    assert_eq!(rows[1]["revision"], 2);
    assert_eq!(rows[1]["assessment"]["marker"], "current");

    let missing = response(&engine, "GET", "/api/medications/missing/history");
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "medication not found");

    fs::remove_file(personal).ok();
}

#[test]
fn medication_stop_preserves_revision_conflict_cleanup_and_history_semantics() {
    let personal = temp_medication_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    assert!(engine.handles_request("DELETE", "/api/medications/m1?expected_revision=2"));
    assert!(!engine.handles_request("PATCH", "/api/medications/m1"));

    let invalid = response(
        &engine,
        "DELETE",
        "/api/medications/m1?expected_revision=bad",
    );
    assert_eq!(invalid["status"], 400);
    assert_eq!(
        invalid["body"]["detail"],
        "expected_revision must be an integer"
    );

    let stale = response(&engine, "DELETE", "/api/medications/m1?expected_revision=1");
    assert_eq!(stale["status"], 409);
    assert_eq!(
        stale["body"]["detail"],
        "expected revision 1, current revision is 2"
    );

    let stopped = response(&engine, "DELETE", "/api/medications/m1?expected_revision=2");
    assert_eq!(stopped["status"], 200);
    assert_eq!(stopped["body"]["active"], false);
    assert_eq!(stopped["body"]["revision"], 3);
    assert_eq!(stopped["body"]["meal_relation"], "after_meal");
    assert_eq!(stopped["body"]["administration_route"], "oral");
    assert_eq!(stopped["body"]["schedules"][0]["time_of_day"], "8:00");
    assert!(stopped["body"]["stopped_at"].as_str().is_some());
    assert!(stopped["body"].get("assessment").is_none());

    let con = Connection::open(&personal).expect("verify stop state");
    let active: i64 = con
        .query_row("SELECT active FROM medications WHERE id='m1'", [], |row| {
            row.get(0)
        })
        .expect("active state");
    assert_eq!(active, 0);
    let remaining = con
        .prepare("SELECT id,status FROM dose_instances ORDER BY id")
        .expect("prepare instance verification")
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .expect("query instances")
        .collect::<Result<Vec<_>, _>>()
        .expect("collect instances");
    assert_eq!(
        remaining,
        vec![
            ("future-taken".to_owned(), "taken".to_owned()),
            ("past-planned".to_owned(), "planned".to_owned()),
        ]
    );
    drop(con);

    let history = response(&engine, "GET", "/api/medications/m1/history");
    let rows = history["body"].as_array().expect("history after stop");
    assert_eq!(rows.len(), 3);
    let stop = &rows[2];
    assert_eq!(stop["revision"], 3);
    assert_eq!(stop["action"], "stop");
    assert_eq!(stop["acknowledged"], false);
    assert!(stop["request_id"].is_null());
    assert!(stop["payload_hash"].is_null());
    assert_eq!(stop["assessment"]["marker"], "current");
    assert_eq!(stop["snapshot"]["active"], false);
    assert_eq!(stop["snapshot"]["revision"], 3);
    assert_eq!(
        stop["snapshot"]["stopped_at"],
        stopped["body"]["stopped_at"]
    );

    let repeated = response(&engine, "DELETE", "/api/medications/m1");
    assert_eq!(repeated["status"], 200);
    assert_eq!(repeated["body"]["revision"], 4);
    assert_eq!(
        repeated["body"]["stopped_at"],
        stopped["body"]["stopped_at"]
    );

    fs::remove_file(personal).ok();
}
