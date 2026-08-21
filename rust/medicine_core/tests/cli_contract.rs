use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_personal_db() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-cli-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create CLI sqlite fixture");
    con.execute_batch(
        "CREATE TABLE people(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            sex TEXT NOT NULL,
            pregnancy_status TEXT NOT NULL,
            lactation_status TEXT NOT NULL DEFAULT 'unknown',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );",
    )
    .expect("create people table");
    drop(con);
    path
}

#[test]
fn request_access_supports_json_for_agent_control() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args(["request-access", "GET", "/api/people", "--json"])
        .output()
        .expect("run medicine-core");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["access"], "personal_read");
}

#[test]
fn health_supports_json_for_agent_control() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "health",
            "--reference-unavailable-reason",
            "fixture",
            "--json",
        ])
        .output()
        .expect("run medicine-core");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["reference_available"], false);
    assert_eq!(value["body"]["reference_status"], "fixture");
}

#[test]
fn generic_request_controls_the_same_people_core() {
    let personal = temp_personal_db();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "request",
            "POST",
            "/api/people",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--body",
            r#"{"name":"CLI","birth_date":"1990-01-01","sex":"male"}"#,
            "--json",
        ])
        .output()
        .expect("run medicine-core request");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("json request output");
    assert_eq!(value["status"], 201);
    assert_eq!(value["body"]["name"], "CLI");
    assert_eq!(value["body"]["pregnancy_status"], "not_applicable");

    fs::remove_file(personal).ok();
}
