#![cfg(feature = "agentctl")]

mod common;

use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::os::unix::fs::PermissionsExt;
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

fn search_reference() -> std::path::PathBuf {
    let path = common::temp_sqlite_path("agentctl-search-reference");
    let connection = Connection::open(&path).expect("create agentctl search fixture");
    connection
        .execute_batch(
            "CREATE TABLE products(
                 item_seq TEXT PRIMARY KEY,
                 product_name TEXT NOT NULL,
                 manufacturer TEXT,
                 ingredient_text TEXT,
                 dosage_form TEXT,
                 permit_date TEXT,
                 cancel_date TEXT,
                 cancel_name TEXT,
                 permit_status TEXT NOT NULL
             );
             CREATE TABLE product_search_documents(
                 item_seq TEXT PRIMARY KEY,
                 normalized_product_name TEXT NOT NULL,
                 normalized_manufacturer TEXT NOT NULL,
                 normalized_ingredient_names TEXT NOT NULL
             );
             CREATE VIRTUAL TABLE product_search_fts USING fts5(
                 searchable_text, tokenize='trigram', content=''
             );
             CREATE TABLE product_rules(
                 id INTEGER PRIMARY KEY,
                 item_seq TEXT NOT NULL,
                 category TEXT NOT NULL,
                 effect_name TEXT
             );
             CREATE TABLE product_criterion_links(
                 product_rule_id INTEGER NOT NULL,
                 criterion_rule_id INTEGER NOT NULL
             );
             INSERT INTO products VALUES(
                 'fixture','Fixture medicine','Fixture manufacturer','fixture ingredient',
                 'tablet','2020-01-01',NULL,NULL,'active'
             );
             INSERT INTO product_search_documents VALUES(
                 'fixture','fixture medicine','fixture manufacturer',
                 char(10)||'fixture ingredient'||char(10)
             );
             INSERT INTO product_search_fts(rowid,searchable_text)
             SELECT rowid,normalized_product_name||char(10)||normalized_manufacturer||normalized_ingredient_names
             FROM product_search_documents;",
        )
        .expect("create agentctl search fixture schema");
    drop(connection);
    path
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
    assert!(value["control"]
        .as_array()
        .expect("control list")
        .iter()
        .any(|item| item == "screenshot"));

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
    assert_eq!(ids, vec!["medicine-engine", "reference-store", "shared-ui"]);
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

#[test]
fn app_commands_preserve_the_developer_cli_surface_without_python() {
    let personal = common::temp_sqlite_path("agentctl-app-commands");
    let add = agentctl()
        .args([
            "--personal-db",
            personal.to_str().expect("personal path"),
            "person-add",
            "--name",
            "Rust",
            "--birth-date",
            "1990-01-01",
            "--sex",
            "male",
            "--json",
        ])
        .output()
        .expect("run person-add through agentctl");
    assert!(
        add.status.success(),
        "{}",
        String::from_utf8_lossy(&add.stderr)
    );
    let added: Value = serde_json::from_slice(&add.stdout).expect("person-add json");
    assert_eq!(added["name"], "Rust");

    let people = agentctl()
        .args([
            "--personal-db",
            personal.to_str().expect("personal path"),
            "people",
            "--json",
        ])
        .output()
        .expect("run people through agentctl");
    assert!(people.status.success());
    let people: Value = serde_json::from_slice(&people.stdout).expect("people json");
    assert_eq!(people.as_array().expect("people array").len(), 1);
    assert_eq!(people[0]["name"], "Rust");
    remove_sqlite(&personal);
}

#[test]
fn app_search_command_preserves_query_options_and_encoding() {
    let personal = common::temp_sqlite_path("agentctl-search-personal");
    let reference = search_reference();
    let output = agentctl()
        .args([
            "--canonical-db",
            reference.to_str().expect("reference path"),
            "--personal-db",
            personal.to_str().expect("personal path"),
            "drug-search",
            "Fixture medicine",
            "--limit",
            "1",
            "--offset",
            "0",
            "--explain-matches",
            "--json",
        ])
        .output()
        .expect("run drug-search through agentctl");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let body: Value = serde_json::from_slice(&output.stdout).expect("drug-search json");
    assert_eq!(body["items"][0]["product_ref"], "fixture");
    assert_eq!(body["items"][0]["search_match"]["tier"], 0);
    assert_eq!(body["has_more"], false);
    remove_sqlite(&personal);
    remove_sqlite(&reference);
}

#[test]
fn app_meds_command_remains_read_only_for_daily_plan_state() {
    let personal = common::temp_sqlite_path("agentctl-meds-read-only");
    let reference = search_reference();
    let add = agentctl()
        .args([
            "--canonical-db",
            reference.to_str().expect("reference path"),
            "--personal-db",
            personal.to_str().expect("personal path"),
            "person-add",
            "--name",
            "Reader",
            "--birth-date",
            "1990-01-01",
            "--sex",
            "male",
            "--json",
        ])
        .output()
        .expect("create person for meds read-only test");
    assert!(add.status.success());
    let added: Value = serde_json::from_slice(&add.stdout).expect("person json");
    let person = added["id"].as_str().expect("person id");

    let connection = Connection::open(&personal).expect("open personal before meds");
    let before: i64 = connection
        .query_row("SELECT COUNT(*) FROM dose_instances", [], |row| row.get(0))
        .expect("count dose instances before meds");
    drop(connection);

    let meds = agentctl()
        .args([
            "--canonical-db",
            reference.to_str().expect("reference path"),
            "--personal-db",
            personal.to_str().expect("personal path"),
            "meds",
            "--person",
            person,
            "--date",
            "2026-08-27",
            "--json",
        ])
        .output()
        .expect("run read-only meds through agentctl");
    assert!(
        meds.status.success(),
        "{}",
        String::from_utf8_lossy(&meds.stderr)
    );
    let body: Value = serde_json::from_slice(&meds.stdout).expect("meds json");
    assert_eq!(body, json!([]));

    let connection = Connection::open(&personal).expect("open personal after meds");
    let after: i64 = connection
        .query_row("SELECT COUNT(*) FROM dose_instances", [], |row| row.get(0))
        .expect("count dose instances after meds");
    assert_eq!(before, after);
    drop(connection);
    remove_sqlite(&personal);
    remove_sqlite(&reference);
}

#[test]
fn screenshot_uses_read_only_personal_snapshot_and_local_rust_web() {
    let root = common::temp_sqlite_path("agentctl-screenshot-root");
    fs::remove_file(&root).expect("release screenshot root path");
    fs::create_dir_all(&root).expect("create screenshot root");
    let static_dir = root.join("static");
    fs::create_dir_all(&static_dir).expect("create screenshot static dir");
    fs::write(static_dir.join("index.html"), "<html>fixture</html>")
        .expect("write screenshot index");

    let canonical = root.join("canonical.sqlite");
    Connection::open(&canonical).expect("create screenshot canonical database");
    let personal = root.join("personal.sqlite");
    let connection = Connection::open(&personal).expect("create screenshot personal database");
    connection
        .execute_batch(
            "CREATE TABLE marker(value TEXT NOT NULL);\n             INSERT INTO marker(value) VALUES('source');",
        )
        .expect("create screenshot marker");
    drop(connection);
    let mut permissions = fs::metadata(&personal)
        .expect("personal metadata")
        .permissions();
    permissions.set_mode(0o444);
    fs::set_permissions(&personal, permissions).expect("make source personal database read only");

    let browser = root.join("fake-chromium");
    let url_log = root.join("browser-url.txt");
    fs::write(
        &browser,
        "#!/bin/sh\nset -eu\nout=''\nurl=''\nfor arg in \"$@\"; do\n  case \"$arg\" in\n    --screenshot=*) out=${arg#--screenshot=} ;;\n    http://*) url=$arg ;;\n  esac\ndone\nprintf '%s' \"$url\" > \"$MEDICINE_SCREENSHOT_URL_LOG\"\nprintf 'png' > \"$out\"\n",
    )
    .expect("write fake chromium");
    let mut browser_permissions = fs::metadata(&browser)
        .expect("browser metadata")
        .permissions();
    browser_permissions.set_mode(0o755);
    fs::set_permissions(&browser, browser_permissions).expect("make fake chromium executable");

    let output = root.join("shot.png");
    let screenshot = agentctl()
        .env(
            "MEDICINE_CORE_WEB_BINARY",
            env!("CARGO_BIN_EXE_medicine-core-web"),
        )
        .env("MEDICINE_CHROMIUM_BINARY", &browser)
        .env("MEDICINE_STATIC_DIR", &static_dir)
        .env("MEDICINE_SCREENSHOT_URL_LOG", &url_log)
        .args([
            "--canonical-db",
            canonical.to_str().expect("canonical path"),
            "--personal-db",
            personal.to_str().expect("personal path"),
            "screenshot",
            "--output",
            output.to_str().expect("output path"),
            "--width",
            "390",
            "--height",
            "844",
            "--screen",
            "meds",
            "--json",
        ])
        .output()
        .expect("run screenshot through agentctl");
    assert!(
        screenshot.status.success(),
        "{}",
        String::from_utf8_lossy(&screenshot.stderr)
    );
    let payload: Value = serde_json::from_slice(&screenshot.stdout).expect("screenshot json");
    assert_eq!(payload["width"], 390);
    assert_eq!(payload["height"], 844);
    assert_eq!(payload["screen"], "meds");
    assert_eq!(payload["size_bytes"], 3);
    assert_eq!(fs::read(&output).expect("read screenshot"), b"png");
    assert!(fs::read_to_string(&url_log)
        .expect("read screenshot URL")
        .ends_with("/?screen=meds"));

    let source = Connection::open_with_flags(&personal, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .expect("reopen source personal database read only");
    let marker: String = source
        .query_row("SELECT value FROM marker", [], |row| row.get(0))
        .expect("read source marker");
    assert_eq!(marker, "source");
    drop(source);

    let mut permissions = fs::metadata(&personal)
        .expect("personal metadata after screenshot")
        .permissions();
    permissions.set_mode(0o644);
    fs::set_permissions(&personal, permissions).expect("restore personal permissions");
    fs::remove_dir_all(root).ok();
}
