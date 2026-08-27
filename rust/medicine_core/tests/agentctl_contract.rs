#![cfg(feature = "agentctl")]

mod common;

use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::Path;
use std::process::Command;

fn agentctl() -> Command {
    Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
}

fn remove_sqlite(path: &Path) {
    fs::remove_file(path).ok();
    fs::remove_file(format!("{}-wal", path.display())).ok();
    fs::remove_file(format!("{}-shm", path.display())).ok();
    fs::remove_file(format!("{}.schema.lock", path.display())).ok();
}

#[test]
fn capabilities_and_targets_expose_exploratory_surface() {
    let capabilities = agentctl()
        .args(["capabilities", "--json"])
        .output()
        .expect("run agentctl capabilities");
    assert!(
        capabilities.status.success(),
        "{}",
        String::from_utf8_lossy(&capabilities.stderr)
    );
    let value: Value = serde_json::from_slice(&capabilities.stdout).expect("capabilities json");
    assert_eq!(value["agentctl"], true);
    assert_eq!(value["scheduled_operations"], true);
    assert_eq!(value["max_scheduled_operations"], 64);
    assert!(value["observation"]
        .as_array()
        .expect("observation list")
        .iter()
        .any(|item| item == "scenario-events"));

    let targets = agentctl()
        .args(["targets", "--json"])
        .output()
        .expect("run agentctl targets");
    assert!(targets.status.success());
    let value: Value = serde_json::from_slice(&targets.stdout).expect("targets json");
    let ids = value["targets"]
        .as_array()
        .expect("targets array")
        .iter()
        .filter_map(|item| item["id"].as_str())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["medicine-engine", "reference-store"]);
}

#[test]
fn scenario_can_schedule_concurrent_mutations_and_emits_structured_events() {
    let personal = common::temp_sqlite_path("agentctl-scenario");
    let input = json!({
        "operations": [
            {
                "id": "person-a",
                "at_ms": 0,
                "method": "POST",
                "path": "/api/people",
                "body": {"name": "A", "birth_date": "1990-01-01", "sex": "male"}
            },
            {
                "id": "person-b",
                "at_ms": 0,
                "method": "POST",
                "path": "/api/people",
                "body": {
                    "name": "B",
                    "birth_date": "1991-01-01",
                    "sex": "female",
                    "pregnancy_status": "not_pregnant",
                    "lactation_status": "not_breastfeeding"
                }
            }
        ]
    })
    .to_string();
    let output = agentctl()
        .args([
            "scenario",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--input",
            &input,
            "--json",
        ])
        .output()
        .expect("run concurrent agentctl scenario");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("scenario json");
    assert_eq!(value["status"], "completed");
    assert_eq!(value["operation_count"], 2);
    let results = value["results"].as_array().expect("scenario results");
    assert_eq!(results.len(), 2);
    assert!(results
        .iter()
        .all(|item| item["response"]["status"].as_u64() == Some(201)));

    let events = String::from_utf8(output.stderr)
        .expect("event utf8")
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).expect("event json"))
        .collect::<Vec<_>>();
    assert_eq!(
        events
            .iter()
            .filter(|event| event["event"] == "operation_started")
            .count(),
        2
    );
    assert_eq!(
        events
            .iter()
            .filter(|event| event["event"] == "operation_completed")
            .count(),
        2
    );

    let connection = Connection::open(&personal).expect("open scenario personal database");
    let people: i64 = connection
        .query_row("SELECT COUNT(*) FROM people", [], |row| row.get(0))
        .expect("count people");
    assert_eq!(people, 2);
    drop(connection);
    remove_sqlite(&personal);
}

#[test]
fn scenario_rejects_duplicate_ids_and_unbounded_horizons_before_execution() {
    let personal = common::temp_sqlite_path("agentctl-invalid-scenario");
    let duplicate = json!({
        "operations": [
            {"id": "same", "at_ms": 0, "method": "GET", "path": "/api/health"},
            {"id": "same", "at_ms": 0, "method": "GET", "path": "/api/health"}
        ]
    })
    .to_string();
    let duplicate_output = agentctl()
        .args([
            "scenario",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--input",
            &duplicate,
            "--json",
        ])
        .output()
        .expect("run duplicate scenario");
    assert!(!duplicate_output.status.success());
    assert!(String::from_utf8_lossy(&duplicate_output.stderr).contains("duplicated"));

    let too_late = json!({
        "operations": [
            {"id": "late", "at_ms": 60_001, "method": "GET", "path": "/api/health"}
        ]
    })
    .to_string();
    let late_output = agentctl()
        .args([
            "scenario",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--input",
            &too_late,
            "--json",
        ])
        .output()
        .expect("run out-of-bounds scenario");
    assert!(!late_output.status.success());
    assert!(String::from_utf8_lossy(&late_output.stderr).contains("60000ms horizon"));
    remove_sqlite(&personal);
}
