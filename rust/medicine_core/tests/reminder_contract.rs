mod common;

use medicine_core::{initialize_personal_db, AccessClass, MedicineEngine};
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn temp_reminder_db() -> PathBuf {
    let path = common::temp_sqlite_path("reminders");
    initialize_personal_db(&path).expect("initialize personal db");
    let con = Connection::open(&path).expect("open reminder fixture");
    con.execute(
        "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status) VALUES(?,?,?,?,?,?)",
        params!["p1", "홍길동", "1990-01-01", "male", "not_applicable", "not_applicable"],
    )
    .expect("insert p1");
    con.execute(
        "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status) VALUES(?,?,?,?,?,?)",
        params!["p2", "김영희", "1992-02-02", "female", "not_pregnant", "not_breastfeeding"],
    )
    .expect("insert p2");

    for (id, person_id, name, as_needed, frequency, start, end, active) in [
        (
            "fixed-a",
            "p1",
            "약A",
            0_i64,
            Some(2_i64),
            "2026-09-01",
            Some("2026-09-10"),
            1_i64,
        ),
        (
            "fixed-b",
            "p2",
            "약B",
            0_i64,
            Some(1_i64),
            "2026-09-01",
            None,
            1_i64,
        ),
        (
            "frequency-only",
            "p1",
            "시간없는약",
            0_i64,
            Some(2_i64),
            "2026-09-01",
            None,
            1_i64,
        ),
        (
            "prn-timed",
            "p1",
            "필요약",
            1_i64,
            None,
            "2026-09-01",
            None,
            1_i64,
        ),
        (
            "inactive",
            "p1",
            "중단약",
            0_i64,
            Some(1_i64),
            "2026-09-01",
            None,
            0_i64,
        ),
    ] {
        con.execute(
            "INSERT INTO medications(id,person_id,product_name,dosage_text,frequency_per_day,as_needed,start_date,end_date,active) VALUES(?,?,?,?,?,?,?,?,?)",
            params![id, person_id, name, "1정", frequency, as_needed, start, end, active],
        )
        .expect("insert medication");
    }
    for (id, medication_id, time) in [
        ("a-1", "fixed-a", "08:00"),
        ("a-2", "fixed-a", "20:00"),
        ("b-1", "fixed-b", "19:30"),
        ("prn-1", "prn-timed", "21:00"),
        ("inactive-1", "inactive", "22:00"),
    ] {
        con.execute(
            "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text) VALUES(?,?,?,NULL)",
            params![id, medication_id, time],
        )
        .expect("insert schedule");
    }
    drop(con);
    path
}

fn request(engine: &MedicineEngine, method: &str, path: &str, body: Value) -> Value {
    serde_json::from_str(&engine.request(method, path, &body.to_string()))
        .expect("decode reminder response")
}

#[test]
fn reminder_upcoming_lists_all_profiles_and_only_future_clock_schedules() {
    let personal = temp_reminder_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    let path = "/api/reminders/upcoming?from=2026-09-03T18%3A00%3A00%2B09%3A00&days=2";

    assert!(engine.handles_request("GET", path));
    assert_eq!(
        engine.request_access("GET", path),
        AccessClass::PersonalRead
    );
    let response = request(&engine, "GET", path, json!({}));
    assert_eq!(response["status"], 200);
    let occurrences = response["body"]["occurrences"]
        .as_array()
        .expect("occurrences");

    assert_eq!(occurrences.len(), 5);
    assert_eq!(occurrences[0]["medication_id"], "fixed-b");
    assert_eq!(occurrences[0]["person_id"], "p2");
    assert_eq!(occurrences[0]["scheduled_at"], "2026-09-03T19:30:00+09:00");
    assert_eq!(occurrences[1]["medication_id"], "fixed-a");
    assert_eq!(occurrences[1]["scheduled_at"], "2026-09-03T20:00:00+09:00");
    assert_eq!(occurrences[4]["scheduled_at"], "2026-09-04T20:00:00+09:00");
    assert!(occurrences
        .iter()
        .all(|item| item.get("product_name").is_none()));
    assert!(occurrences
        .iter()
        .all(|item| item["medication_id"] != "frequency-only"));
    assert!(occurrences
        .iter()
        .all(|item| item["medication_id"] != "prn-timed"));
    assert!(occurrences
        .iter()
        .all(|item| item["medication_id"] != "inactive"));

    fs::remove_file(personal).ok();
}

#[test]
fn reminder_resolve_materializes_authoritative_instance_and_suppresses_stale_or_completed_alarm() {
    let personal = temp_reminder_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);
    let resolve_path = "/api/reminders/resolve";
    let key = json!({
        "person_id": "p1",
        "medication_id": "fixed-a",
        "scheduled_date": "2026-09-03",
        "schedule_key": "slot:2",
        "scheduled_at": "2026-09-03T20:00:00+09:00"
    });

    assert!(engine.handles_request("POST", resolve_path));
    assert_eq!(
        engine.request_access("POST", resolve_path),
        AccessClass::PersonalWrite
    );
    let resolved = request(&engine, "POST", resolve_path, key.clone());
    assert_eq!(resolved["status"], 200);
    assert_eq!(resolved["body"]["active"], true);
    assert_eq!(resolved["body"]["product_name"], "약A");
    assert_eq!(resolved["body"]["person_name"], "홍길동");
    assert_eq!(resolved["body"]["dose_text"], "1정");
    assert_eq!(
        resolved["body"]["scheduled_at"],
        "2026-09-03T20:00:00+09:00"
    );
    let instance_id = resolved["body"]["dose_instance_id"]
        .as_str()
        .expect("dose instance id")
        .to_owned();

    let completed = request(
        &engine,
        "POST",
        &format!("/api/dose-instances/{instance_id}"),
        json!({"status":"taken","occurred_at":"2026-09-03T20:01:00+09:00"}),
    );
    assert_eq!(completed["status"], 200);
    let after_completion = request(&engine, "POST", resolve_path, key.clone());
    assert_eq!(after_completion["status"], 200);
    assert_eq!(after_completion["body"], json!({"active": false}));

    let con = Connection::open(&personal).expect("change schedule");
    con.execute(
        "UPDATE medication_schedules SET time_of_day='21:00' WHERE medication_id='fixed-a' AND time_of_day='08:00'",
        [],
    )
    .expect("change first schedule");
    drop(con);
    let stale = request(
        &engine,
        "POST",
        resolve_path,
        json!({
            "person_id": "p1",
            "medication_id": "fixed-a",
            "scheduled_date": "2026-09-04",
            "schedule_key": "slot:1",
            "scheduled_at": "2026-09-04T08:00:00+09:00"
        }),
    );
    assert_eq!(stale["status"], 200);
    assert_eq!(stale["body"], json!({"active": false}));

    let con = Connection::open(&personal).expect("stop medication");
    con.execute("UPDATE medications SET active=0 WHERE id='fixed-a'", [])
        .expect("stop medication");
    drop(con);
    let stopped = request(&engine, "POST", resolve_path, key);
    assert_eq!(stopped["status"], 200);
    assert_eq!(stopped["body"], json!({"active": false}));

    fs::remove_file(personal).ok();
}

#[test]
fn reminder_routes_validate_inputs_without_reference_database() {
    let personal = temp_reminder_db();
    let engine = MedicineEngine::new(None, Some(personal.as_path()), Some("update_required"));

    let bad_days = request(
        &engine,
        "GET",
        "/api/reminders/upcoming?from=2026-09-03T18%3A00%3A00%2B09%3A00&days=0",
        json!({}),
    );
    assert_eq!(bad_days["status"], 400);

    let bad_resolve = request(
        &engine,
        "POST",
        "/api/reminders/resolve",
        json!({"person_id":"p1"}),
    );
    assert_eq!(bad_resolve["status"], 400);
    assert_ne!(bad_resolve["status"], 503);

    fs::remove_file(personal).ok();
}
