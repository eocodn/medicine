use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_dose_db() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-dose-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create dose fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE people(
             id TEXT PRIMARY KEY,
             name TEXT NOT NULL,
             birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,
             pregnancy_status TEXT NOT NULL,
             lactation_status TEXT NOT NULL
         );
         CREATE TABLE medications(
             id TEXT PRIMARY KEY,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
             product_name TEXT NOT NULL,
             ingredient_name TEXT,
             dosage_text TEXT
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
             VALUES('p1','Dose','1990-01-01','male','not_applicable','not_applicable');
         INSERT INTO medications(id,person_id,product_name,ingredient_name,dosage_text)
             VALUES('m1','p1','약A','성분A','1정');
         INSERT INTO dose_instances(
             id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
             slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status
         ) VALUES('d1','m1','p1','2026-08-20','slot:1','08:00',NULL,'1정','약A','성분A','planned');
         INSERT INTO dose_instances(
             id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
             slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status,completed_at
         ) VALUES('prn1','m1','p1','2026-08-20','prn:prn1',NULL,'필요시','1정','약A','성분A','taken','2026-08-20T12:00:00+09:00');
         INSERT INTO dose_logs(
             id,medication_id,person_id,status,occurred_at,note,dose_instance_id,
             product_name_snapshot,dosage_text_snapshot
         ) VALUES('log-prn','m1','p1','taken','2026-08-20T12:00:00+09:00','증상 시','prn1','약A','1정');
         INSERT INTO prn_requests(request_id,medication_id,person_id,payload_hash,dose_instance_id,state)
             VALUES('req-prn','m1','p1','hash','prn1','active');",
    )
    .expect("create dose schema and fixtures");
    drop(con);
    path
}

fn request(engine: &MedicineEngine, method: &str, path: &str, body: Value) -> Value {
    serde_json::from_str(&engine.request(method, path, &body.to_string()))
        .expect("decode dose response")
}

fn recent_log<'a>(response: &'a Value, instance_id: &str) -> &'a Value {
    response["body"]["recent_logs"]
        .as_array()
        .expect("recent logs")
        .iter()
        .find(|item| item["dose_instance_id"] == instance_id)
        .expect("dose instance recent log")
}

#[test]
fn scheduled_dose_transition_is_atomic_and_same_state_retry_preserves_metadata() {
    let personal = temp_dose_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    assert!(engine.handles_request("POST", "/api/dose-instances/d1"));

    let first = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({
            "status": "taken",
            "occurred_at": "2026-08-20T08:05:00+09:00",
            "note": "memo"
        }),
    );
    assert_eq!(first["status"], 200);
    assert_eq!(first["body"]["status"], "taken");
    assert_eq!(first["body"]["completed_at"], "2026-08-20T08:05:00+09:00");
    assert_eq!(recent_log(&first, "d1")["note"], "memo");
    assert_eq!(recent_log(&first, "d1")["product_name"], "약A");
    assert_eq!(recent_log(&first, "d1")["dosage_text"], "1정");

    let repeated = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"status": "taken"}),
    );
    assert_eq!(repeated["status"], 200);
    assert_eq!(
        repeated["body"]["completed_at"],
        "2026-08-20T08:05:00+09:00"
    );
    assert_eq!(
        recent_log(&repeated, "d1")["occurred_at"],
        "2026-08-20T08:05:00+09:00"
    );
    assert_eq!(recent_log(&repeated, "d1")["note"], "memo");

    let cleared = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"status": "taken", "note": null}),
    );
    assert_eq!(cleared["status"], 200);
    assert_ne!(cleared["body"]["completed_at"], "2026-08-20T08:05:00+09:00");
    assert!(recent_log(&cleared, "d1")["note"].is_null());

    let con = Connection::open(&personal).expect("verify dose state");
    let log_count: i64 = con
        .query_row(
            "SELECT COUNT(*) FROM dose_logs WHERE dose_instance_id='d1'",
            [],
            |row| row.get(0),
        )
        .expect("dose log count");
    assert_eq!(log_count, 1);
    drop(con);
    fs::remove_file(personal).ok();
}

#[test]
fn scheduled_cancel_is_idempotent_and_removes_its_log() {
    let personal = temp_dose_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"status":"skipped","occurred_at":"2026-08-20T08:10:00+09:00"}),
    );

    for _ in 0..2 {
        let canceled = request(
            &engine,
            "DELETE",
            "/api/dose-instances/d1/completion",
            json!({}),
        );
        assert_eq!(canceled["status"], 200);
        assert_eq!(canceled["body"]["status"], "planned");
        assert!(canceled["body"]["completed_at"].is_null());
        assert_eq!(
            canceled["body"]["recent_logs"]
                .as_array()
                .expect("recent logs")
                .iter()
                .filter(|item| item["dose_instance_id"] == "d1")
                .count(),
            0
        );
    }

    fs::remove_file(personal).ok();
}

#[test]
fn prn_cancel_keeps_exact_retry_tombstone_semantics() {
    let personal = temp_dose_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    for _ in 0..2 {
        let canceled = request(
            &engine,
            "DELETE",
            "/api/dose-instances/prn1/completion",
            json!({}),
        );
        assert_eq!(canceled["status"], 200);
        assert_eq!(canceled["body"]["id"], "prn1");
        assert_eq!(canceled["body"]["status"], "canceled");
        assert_eq!(canceled["body"]["deleted"], true);
        assert!(canceled["body"]["completed_at"].is_null());
        assert!(canceled["body"]["recent_logs"]
            .as_array()
            .expect("recent logs")
            .is_empty());
    }

    let con = Connection::open(&personal).expect("verify PRN tombstone");
    let state: String = con
        .query_row(
            "SELECT state FROM prn_requests WHERE dose_instance_id='prn1'",
            [],
            |row| row.get(0),
        )
        .expect("PRN state");
    assert_eq!(state, "canceled");
    let instance_count: i64 = con
        .query_row(
            "SELECT COUNT(*) FROM dose_instances WHERE id='prn1'",
            [],
            |row| row.get(0),
        )
        .expect("PRN instance count");
    assert_eq!(instance_count, 0);
    drop(con);
    fs::remove_file(personal).ok();
}

#[test]
fn scheduled_dose_routes_keep_validation_and_not_found_envelopes() {
    let personal = temp_dose_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    let missing_status = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"note":"x"}),
    );
    assert_eq!(missing_status["status"], 400);
    assert_eq!(missing_status["body"]["detail"], "status is required");

    let invalid_status = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"status":"planned"}),
    );
    assert_eq!(invalid_status["status"], 400);
    assert_eq!(
        invalid_status["body"]["detail"],
        "status must be taken or skipped"
    );

    let unknown = request(
        &engine,
        "POST",
        "/api/dose-instances/d1",
        json!({"status":"taken","extra":true}),
    );
    assert_eq!(unknown["status"], 400);
    assert_eq!(unknown["body"]["detail"], "unknown fields: extra");

    let missing = request(
        &engine,
        "DELETE",
        "/api/dose-instances/never/completion",
        json!({}),
    );
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "dose instance not found");

    fs::remove_file(personal).ok();
}
