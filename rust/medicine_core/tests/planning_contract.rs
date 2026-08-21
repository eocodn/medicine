use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_planning_db() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-planning-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create planning fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE people(
             id TEXT PRIMARY KEY,
             name TEXT NOT NULL,
             birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,
             pregnancy_status TEXT NOT NULL,
             lactation_status TEXT NOT NULL,
             notes TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
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
             stopped_at TEXT,
             revision INTEGER NOT NULL DEFAULT 1,
             updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
             action TEXT,
             snapshot_json TEXT,
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
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
             UNIQUE(medication_id, scheduled_date, schedule_key)
         );
         CREATE TABLE dose_logs(
             id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
             status TEXT NOT NULL,
             occurred_at TEXT NOT NULL,
             note TEXT,
             dose_instance_id TEXT,
             product_name_snapshot TEXT,
             dosage_text_snapshot TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         CREATE TABLE prn_requests(
             request_id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
             payload_hash TEXT NOT NULL,
             dose_instance_id TEXT NOT NULL,
             state TEXT NOT NULL CHECK(state IN ('active','canceled')),
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status)
             VALUES('p1','Plan','1990-01-01','male','not_applicable','not_applicable');
         INSERT INTO medications(
             id,person_id,product_name,ingredient_name,dosage_text,start_date,end_date,
             frequency_per_day,meal_relation,administration_route
         ) VALUES('scheduled','p1','아침약','성분A','1정','2026-08-10','2026-08-12',2,'after_meal','oral');
         INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
             VALUES('s-late','scheduled','20:00',NULL),('s-early','scheduled','8:00','반정');
         INSERT INTO medications(
             id,person_id,product_name,dosage_text,start_date,frequency_per_day
         ) VALUES('frequency','p1','횟수약','2정','2026-08-10',2);
         INSERT INTO medications(
             id,person_id,product_name,dosage_text,start_date,as_needed,prn_max_per_day
         ) VALUES('prn','p1','필요약','1정','2026-08-10',1,3);
         INSERT INTO medications(
             id,person_id,product_name,dosage_text,start_date,as_needed,prn_max_per_day
         ) VALUES('prn-timed','p1','시간있는 필요약','1정','2026-08-10',1,3);
         INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
             VALUES('prn-time','prn-timed','07:00',NULL);
         INSERT INTO medications(id,person_id,product_name,dosage_text,start_date)
             VALUES('unscheduled','p1','무일정약','1정','2026-08-10');
         INSERT INTO medications(id,person_id,product_name,dosage_text,start_date,end_date)
             VALUES('future','p1','미래약','1정','2026-08-11','2026-08-20');",
    )
    .expect("create planning schema and fixtures");
    drop(con);
    path
}

fn get(engine: &MedicineEngine, path: &str) -> Value {
    serde_json::from_str(&engine.request("GET", path, "")).expect("decode planning response")
}

#[test]
fn daily_plan_materializes_stable_sorted_occurrences_and_tracks_completion() {
    let personal = temp_planning_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    let path = "/api/people/p1/daily-plan?date=2026-08-10";

    assert!(engine.handles_request("GET", path));
    let first = get(&engine, path);
    assert_eq!(first["status"], 200);
    assert_eq!(first["body"]["date"], "2026-08-10");
    let doses = first["body"]["doses"].as_array().expect("dose array");
    assert_eq!(doses.len(), 4);
    assert_eq!(doses[0]["medication_id"], "scheduled");
    assert_eq!(doses[0]["scheduled_time"], "8:00");
    assert_eq!(doses[0]["dose_text"], "반정");
    assert_eq!(doses[1]["scheduled_time"], "20:00");
    assert_eq!(doses[2]["medication_id"], "frequency");
    assert_eq!(doses[2]["slot_label"], "1회차");
    assert_eq!(doses[3]["slot_label"], "2회차");
    assert_eq!(first["body"]["summary"]["planned"], 4);
    assert_eq!(first["body"]["summary"]["taken"], 0);
    assert_eq!(first["body"]["summary"]["skipped"], 0);
    assert_eq!(first["body"]["prn_medications"][0]["id"], "prn-timed");
    assert_eq!(first["body"]["prn_medications"][1]["id"], "prn");
    assert_eq!(
        first["body"]["unscheduled_medications"][0]["id"],
        "unscheduled"
    );
    assert!(first["body"]["prn_medications"][0]["as_needed"]
        .as_bool()
        .expect("PRN boolean"));
    assert!(!first["body"]["prn_medications"]
        .as_array()
        .expect("PRN array")
        .iter()
        .any(|item| item["id"] == "future"));

    let first_ids = doses
        .iter()
        .map(|dose| dose["id"].as_str().expect("dose id").to_owned())
        .collect::<Vec<_>>();
    let observer = Connection::open(&personal).expect("open data version observer");
    let version_before: i64 = observer
        .query_row("PRAGMA data_version", [], |row| row.get(0))
        .expect("data version before");
    let second = get(&engine, path);
    let version_after: i64 = observer
        .query_row("PRAGMA data_version", [], |row| row.get(0))
        .expect("data version after");
    let second_ids = second["body"]["doses"]
        .as_array()
        .expect("second dose array")
        .iter()
        .map(|dose| dose["id"].as_str().expect("dose id").to_owned())
        .collect::<Vec<_>>();
    assert_eq!(second_ids, first_ids);
    assert_eq!(version_after, version_before);
    drop(observer);

    let completed: Value = serde_json::from_str(&engine.request(
        "POST",
        &format!("/api/dose-instances/{}", first_ids[0]),
        r#"{"status":"taken","occurred_at":"2026-08-10T08:05:00+09:00"}"#,
    ))
    .expect("decode completed response");
    assert_eq!(completed["status"], 200);
    let refreshed = get(&engine, path);
    assert_eq!(refreshed["body"]["doses"][0]["status"], "taken");
    assert_eq!(refreshed["body"]["summary"]["planned"], 3);
    assert_eq!(refreshed["body"]["summary"]["taken"], 1);

    fs::remove_file(personal).ok();
}

#[test]
fn daily_plan_reconciles_only_planned_rows_and_preserves_completed_history() {
    let personal = temp_planning_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    let path = "/api/people/p1/daily-plan?date=2026-08-10";
    let first = get(&engine, path);
    let first_id = first["body"]["doses"][0]["id"]
        .as_str()
        .expect("first occurrence")
        .to_owned();
    let second_id = first["body"]["doses"][1]["id"]
        .as_str()
        .expect("second occurrence")
        .to_owned();

    let completed: Value = serde_json::from_str(&engine.request(
        "POST",
        &format!("/api/dose-instances/{first_id}"),
        r#"{"status":"taken","occurred_at":"2026-08-10T08:05:00+09:00"}"#,
    ))
    .expect("decode completion");
    assert_eq!(completed["status"], 200);

    let con = Connection::open(&personal).expect("change schedules");
    con.execute(
        "DELETE FROM medication_schedules WHERE medication_id='scheduled'",
        [],
    )
    .expect("remove schedules");
    con.execute(
        "UPDATE medications SET frequency_per_day=NULL WHERE id='scheduled'",
        [],
    )
    .expect("remove fallback frequency");
    drop(con);

    let reconciled = get(&engine, path);
    let doses = reconciled["body"]["doses"]
        .as_array()
        .expect("reconciled doses");
    assert!(doses
        .iter()
        .any(|dose| dose["id"] == first_id && dose["status"] == "taken"));
    assert!(!doses.iter().any(|dose| dose["id"] == second_id));
    assert!(reconciled["body"]["unscheduled_medications"]
        .as_array()
        .expect("unscheduled array")
        .iter()
        .any(|item| item["id"] == "scheduled"));

    fs::remove_file(personal).ok();
}

#[test]
fn daily_plan_keeps_http_validation_and_not_found_envelopes() {
    let personal = temp_planning_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    let missing = get(&engine, "/api/people/missing/daily-plan?date=2026-08-10");
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "person not found");

    let invalid = get(&engine, "/api/people/p1/daily-plan?date=bad");
    assert_eq!(invalid["status"], 400);
    assert_eq!(invalid["body"]["detail"], "Invalid isoformat string: 'bad'");

    fs::remove_file(personal).ok();
}
