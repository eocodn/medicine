mod common;

use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn temp_personal_db() -> PathBuf {
    let path = common::temp_sqlite_path("cli");
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

fn temp_personal_db_path() -> PathBuf {
    common::temp_sqlite_path("cli-personal-schema")
}

fn temp_canonical_db() -> PathBuf {
    let path = common::temp_sqlite_path("cli-canonical");
    let con = Connection::open(&path).expect("create CLI canonical fixture");
    con.execute_batch(
        "CREATE TABLE canonical_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
         CREATE TABLE source_snapshots(
             dataset_key TEXT PRIMARY KEY,source_family TEXT NOT NULL,sha256 TEXT NOT NULL,
             row_count INTEGER NOT NULL,fetched_at TEXT,effective_date TEXT
         );
         CREATE TABLE products(
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
         CREATE TABLE product_identifiers(item_seq TEXT,system TEXT,value TEXT);
         CREATE TABLE product_rules(
             id INTEGER PRIMARY KEY,category TEXT,item_seq TEXT,paired_item_seq TEXT,
             effect_name TEXT,dosage_form TEXT,source_dataset_key TEXT,source_row INTEGER
         );
         CREATE TABLE product_flags(
             item_seq TEXT,category TEXT,flag_code TEXT,flag_name TEXT,ingredient_name TEXT,
             dosage_form TEXT,details TEXT,change_date TEXT,source_dataset_key TEXT,
             source_row INTEGER,flag_ordinal INTEGER
         );
         CREATE TABLE product_criterion_links(product_rule_id INTEGER,criterion_rule_id INTEGER);
         CREATE TABLE product_rule_criteria(
             product_source_dataset_key TEXT,product_source_row INTEGER,
             criterion_source_dataset_key TEXT,criterion_source_row INTEGER,
             item_seq TEXT,category TEXT
         );
         INSERT INTO canonical_meta(key,value) VALUES
             ('schema_version','10'),('build_stage','complete'),('built_at','2026-08-13T15:00:00+09:00');
         INSERT INTO source_snapshots(dataset_key,source_family,sha256,row_count)
         VALUES('fixture','mfds_permit_api','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1);
         INSERT INTO products(
             item_seq,product_name,manufacturer,ingredient_text,dosage_form,permit_status
         ) VALUES('P-CLI','CLI 약','제약','Drug','정제','active');",
    )
    .expect("create CLI canonical tables");
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
fn personal_schema_command_initializes_the_shared_rust_schema() {
    let personal = temp_personal_db_path();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "personal-schema",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--json",
        ])
        .output()
        .expect("run personal-schema command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("personal schema json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["schema_version"], 1);
    assert_eq!(value["body"]["initialized"], true);

    let connection = Connection::open(&personal).expect("open initialized personal database");
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
        let exists: bool = connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
                [table],
                |row| row.get(0),
            )
            .expect("inspect initialized schema");
        assert!(exists, "missing shared Rust table {table}");
    }
    drop(connection);

    let second = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "personal-schema",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--json",
        ])
        .output()
        .expect("rerun personal-schema command");
    assert!(second.status.success());
    let second_value: Value = serde_json::from_slice(&second.stdout).expect("second schema json");
    assert_eq!(second_value["status"], 200);
    assert_eq!(second_value["body"]["schema_version"], 1);
    assert_eq!(second_value["body"]["initialized"], true);
    fs::remove_file(&personal).ok();
    fs::remove_file(format!("{}.schema.lock", personal.display())).ok();
}

#[test]
fn personal_checkpoint_command_exposes_structured_success_and_usage_failure() {
    let personal = temp_personal_db_path();
    let initialize = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "personal-schema",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--json",
        ])
        .output()
        .expect("initialize personal database");
    assert!(initialize.status.success());

    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "personal-checkpoint",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--json",
        ])
        .output()
        .expect("run personal-checkpoint command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("checkpoint json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["checkpointed"], true);

    let missing_path = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args(["personal-checkpoint", "--json"])
        .output()
        .expect("run checkpoint usage failure");
    assert!(!missing_path.status.success());
    assert!(String::from_utf8_lossy(&missing_path.stderr).contains("personal-checkpoint"));

    fs::remove_file(&personal).ok();
    fs::remove_file(format!("{}.schema.lock", personal.display())).ok();
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

#[test]
fn product_command_uses_the_same_canonical_product_core() {
    let canonical = temp_canonical_db();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "product",
            "--canonical-db",
            canonical.to_str().expect("canonical path"),
            "--product-ref",
            "P-CLI",
            "--json",
        ])
        .output()
        .expect("run product command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("product json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["product_name"], "CLI 약");
    assert_eq!(value["body"]["suggested_administration_route"], "oral");
    fs::remove_file(canonical).ok();
}

#[test]
fn draft_normalize_command_exposes_python_compatible_hash() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "draft-normalize",
            "--body",
            r#"{"dose_amount":"1.00","dose_unit":"정","schedule_times":["8:00","20:00"],"prescription_days":"5","start_date":"2026-08-20"}"#,
            "--person",
            "person-1",
            "--product-ref",
            "P-Z",
            "--json",
        ])
        .output()
        .expect("run draft-normalize command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("draft json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["draft"]["frequency_per_day"], 2);
    assert_eq!(
        value["body"]["draft_hash"],
        "538f7291c8f1d65e3e88b9e4b6db3a32572859b8c18616b2ffb0aa0a3e612de9"
    );
}

#[test]
fn safety_basis_command_exposes_reference_and_quantitative_core() {
    let canonical = temp_canonical_db();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "safety-basis",
            "--canonical-db",
            canonical.to_str().expect("canonical path"),
            "--product-ref",
            "P-CLI",
            "--person",
            r#"{"birth_date":"1990-01-01","sex":"male","pregnancy_status":"not_applicable"}"#,
            "--draft",
            r#"{"long_term":true}"#,
            "--json",
        ])
        .output()
        .expect("run safety-basis command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("safety basis json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["dataset"]["status"], "verified");
    assert_eq!(value["body"]["coverage"]["status"], "complete");
    assert_eq!(
        value["body"]["quantitative_checks"]["duration"]["result"],
        "not_applicable"
    );
    assert_eq!(
        value["body"]["quantitative_checks"]["dose"]["result"],
        "not_applicable"
    );
    fs::remove_file(canonical).ok();
}

#[test]
fn dur_display_command_uses_the_same_display_core() {
    let payload = r#"{
        "person":{"birth_date":"1990-01-01","sex":"male","pregnancy_status":"not_applicable"},
        "current":[],
        "risks":[],
        "duration":{"result":"not_applicable","source_scope":"canonical_product","source_rows":[]},
        "dose":{"result":"not_applicable","source_scope":"canonical_product","source_rows":[]},
        "coverage":{"status":"complete","product":{"status":"matched"},"category_resolution":{},"not_evaluable_checks":[]},
        "dataset":{"status":"verified"},
        "candidate_course":{"start_date":"2026-08-22"},
        "product":{"product_flags":[]},
        "detailed_product_categories":[],
        "review_items":[],
        "as_of":"2026-08-22"
    }"#;
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args(["dur-display", "--input", payload, "--json"])
        .output()
        .expect("run dur-display command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("DUR display json");
    assert_eq!(value["status"], 200);
    assert_eq!(
        value["body"]["dur_checks"].as_array().map(Vec::len),
        Some(7)
    );
    assert_eq!(value["body"]["requires_review"], false);
}

#[test]
fn profile_risks_command_uses_the_same_profile_safety_core() {
    let canonical = temp_canonical_db();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "profile-risks",
            "--canonical-db",
            canonical.to_str().expect("canonical path"),
            "--product-ref",
            "P-CLI",
            "--person",
            r#"{"birth_date":"1990-01-01","sex":"male","pregnancy_status":"not_applicable"}"#,
            "--course",
            r#"{"start_date":"2026-08-22"}"#,
            "--as-of",
            "2026-08-22",
            "--json",
        ])
        .output()
        .expect("run profile-risks command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("profile risks json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["product"]["product_ref"], "P-CLI");
    assert_eq!(value["body"]["risks"], serde_json::json!([]));
    fs::remove_file(canonical).ok();
}

#[test]
fn interaction_risks_command_uses_the_same_interaction_core() {
    let canonical = temp_canonical_db();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "interaction-risks",
            "--canonical-db",
            canonical.to_str().expect("canonical path"),
            "--product-ref",
            "P-CLI",
            "--current",
            "[]",
            "--course",
            r#"{"start_date":"2026-08-22"}"#,
            "--json",
        ])
        .output()
        .expect("run interaction-risks command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("interaction risks json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["risks"], serde_json::json!([]));
    fs::remove_file(canonical).ok();
}
