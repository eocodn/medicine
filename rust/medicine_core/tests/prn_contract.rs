use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_prn_db() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-prn-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create PRN fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE people(id TEXT PRIMARY KEY);
         CREATE TABLE medications(
             id TEXT PRIMARY KEY,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
             product_name TEXT NOT NULL,
             ingredient_name TEXT,
             dosage_text TEXT,
             start_date TEXT,
             end_date TEXT,
             active INTEGER NOT NULL DEFAULT 1,
             as_needed INTEGER NOT NULL DEFAULT 0,
             prn_max_per_day INTEGER
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
         INSERT INTO people(id) VALUES('p1');
         INSERT INTO medications(
             id,person_id,product_name,ingredient_name,dosage_text,start_date,end_date,
             active,as_needed,prn_max_per_day
         ) VALUES('prn','p1','필요약','성분A','1정','2026-08-20','2026-08-20',1,1,1);
         INSERT INTO medications(
             id,person_id,product_name,dosage_text,start_date,active,as_needed
         ) VALUES('scheduled','p1','정규약','1정','2026-08-20',1,0);",
    )
    .expect("create PRN schema and fixtures");
    drop(con);
    path
}

fn request(engine: &MedicineEngine, medication_id: &str, body: Value) -> Value {
    serde_json::from_str(&engine.request(
        "POST",
        &format!("/api/medications/{medication_id}/prn-intakes"),
        &body.to_string(),
    ))
    .expect("decode PRN response")
}

#[test]
fn prn_request_id_is_exactly_once_and_conflicting_reuse_is_rejected() {
    let personal = temp_prn_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    assert!(engine.handles_request("POST", "/api/medications/prn/prn-intakes"));

    let payload = json!({
        "request_id": "prn-one",
        "occurred_at": "2026-08-20T12:00:00+09:00",
        "note": "증상 시"
    });
    let first = request(&engine, "prn", payload.clone());
    assert_eq!(first["status"], 201);
    assert_eq!(first["body"]["status"], "taken");
    assert_eq!(first["body"]["slot_label"], "필요시");
    assert!(first["body"]["schedule_key"]
        .as_str()
        .expect("schedule key")
        .starts_with("prn:"));
    assert_eq!(first["body"]["recent_logs"].as_array().unwrap().len(), 1);
    assert_eq!(first["body"]["recent_logs"][0]["request_id"], "prn-one");

    let con = Connection::open(&personal).expect("verify PRN request hash");
    let payload_hash: String = con
        .query_row(
            "SELECT payload_hash FROM prn_requests WHERE request_id='prn-one'",
            [],
            |row| row.get(0),
        )
        .expect("PRN request payload hash");
    assert_eq!(
        payload_hash,
        "902bc030206918bc37c48a47f26123a5d87b933bbf6ec3bd45c2d298a9dbab93"
    );
    drop(con);

    let repeated = request(&engine, "prn", payload);
    assert_eq!(repeated["status"], 201);
    assert_eq!(repeated["body"]["id"], first["body"]["id"]);
    assert_eq!(repeated["body"]["recent_logs"].as_array().unwrap().len(), 1);

    let conflict = request(
        &engine,
        "prn",
        json!({
            "request_id": "prn-one",
            "occurred_at": "2026-08-20T13:00:00+09:00",
            "note": "다른 증상"
        }),
    );
    assert_eq!(conflict["status"], 409);
    assert!(conflict["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("request_id"));

    let instance_id = first["body"]["id"].as_str().unwrap();
    let canceled: Value = serde_json::from_str(&engine.request(
        "DELETE",
        &format!("/api/dose-instances/{instance_id}/completion"),
        "",
    ))
    .expect("decode cancel response");
    assert_eq!(canceled["status"], 200);
    assert_eq!(canceled["body"]["deleted"], true);

    let canceled_retry = request(
        &engine,
        "prn",
        json!({
            "request_id": "prn-one",
            "occurred_at": "2026-08-20T12:00:00+09:00",
            "note": "증상 시"
        }),
    );
    assert_eq!(canceled_retry["status"], 409);
    assert!(canceled_retry["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("canceled"));

    fs::remove_file(personal).ok();
}

#[test]
fn prn_daily_maximum_uses_korea_date_and_active_course() {
    let personal = temp_prn_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    let first = request(
        &engine,
        "prn",
        json!({
            "request_id": "offset-one",
            "occurred_at": "2026-08-19T16:00:00Z"
        }),
    );
    assert_eq!(first["status"], 201);
    assert_eq!(first["body"]["scheduled_date"], "2026-08-20");

    let blocked = request(
        &engine,
        "prn",
        json!({
            "request_id": "offset-two",
            "occurred_at": "2026-08-20T14:00:00+09:00"
        }),
    );
    assert_eq!(blocked["status"], 400);
    assert!(blocked["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("maximum"));

    let outside_course = request(
        &engine,
        "prn",
        json!({
            "request_id": "next-day",
            "occurred_at": "2026-08-20T15:00:00Z"
        }),
    );
    assert_eq!(outside_course["status"], 400);
    assert!(outside_course["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("not active"));

    let non_prn = request(
        &engine,
        "scheduled",
        json!({
            "request_id": "not-prn",
            "occurred_at": "2026-08-20T12:00:00+09:00"
        }),
    );
    assert_eq!(non_prn["status"], 400);
    assert!(non_prn["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("not PRN"));

    fs::remove_file(personal).ok();
}

#[test]
fn prn_route_keeps_mobile_validation_envelopes() {
    let personal = temp_prn_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    let missing = request(
        &engine,
        "prn",
        json!({"occurred_at": "2026-08-20T12:00:00+09:00"}),
    );
    assert_eq!(missing["status"], 400);
    assert_eq!(missing["body"]["detail"], "request_id is required");

    let blank = request(
        &engine,
        "prn",
        json!({
            "request_id": "   ",
            "occurred_at": "2026-08-20T12:00:00+09:00"
        }),
    );
    assert_eq!(blank["status"], 400);
    assert_eq!(blank["body"]["detail"], "request_id is required");

    let unknown = request(
        &engine,
        "prn",
        json!({
            "request_id": "unknown-field",
            "unexpected": true
        }),
    );
    assert_eq!(unknown["status"], 400);
    assert_eq!(unknown["body"]["detail"], "unknown fields: unexpected");

    let invalid_time = request(
        &engine,
        "prn",
        json!({
            "request_id": "invalid-time",
            "occurred_at": "not-a-time"
        }),
    );
    assert_eq!(invalid_time["status"], 400);

    let missing_medication = request(
        &engine,
        "missing",
        json!({
            "request_id": "missing-med",
            "occurred_at": "2026-08-20T12:00:00+09:00"
        }),
    );
    assert_eq!(missing_medication["status"], 404);
    assert_eq!(missing_medication["body"]["detail"], "medication not found");

    fs::remove_file(personal).ok();
}
