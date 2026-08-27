#![cfg(feature = "agentctl")]

mod common;

use flate2::{write::GzEncoder, Compression};
use medicine_core::reference_state::{ReferenceStateCodec, ReferenceStoreState, ReferenceVersion};
use rusqlite::Connection;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args(["request-access", "GET", "/api/people", "--json"])
        .output()
        .expect("run medicine-agentctl");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["access"], "personal_read");
}

#[test]
fn usage_names_the_agentctl_control_surface() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .arg("not-a-command")
        .output()
        .expect("run medicine-agentctl usage failure");
    assert_eq!(output.status.code(), Some(2));
    let stderr = String::from_utf8(output.stderr).expect("utf8 stderr");
    assert!(stderr.contains("usage: medicine-agentctl"));
}

#[test]
fn health_supports_json_for_agent_control() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "health",
            "--reference-unavailable-reason",
            "fixture",
            "--json",
        ])
        .output()
        .expect("run medicine-agentctl");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["reference_available"], false);
    assert_eq!(value["body"]["reference_status"], "fixture");
}

#[test]
fn personal_schema_command_initializes_the_shared_rust_schema() {
    let personal = temp_personal_db_path();
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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

    let second = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let initialize = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "personal-schema",
            "--personal-db",
            personal.to_str().expect("personal path"),
            "--json",
        ])
        .output()
        .expect("initialize personal database");
    assert!(initialize.status.success());

    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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

    let missing_path = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
        .expect("run medicine-agentctl request");
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
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

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn encode_gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(bytes).expect("write gzip fixture");
    encoder.finish().expect("finish gzip fixture")
}

fn put_utf(output: &mut Vec<u8>, value: &str) {
    output.extend_from_slice(&(value.len() as u16).to_be_bytes());
    output.extend_from_slice(value.as_bytes());
}

const PATCH_SOURCE: &[u8] = b"abcdefgh";
const PATCH_TARGET: &[u8] = b"abCDefgh!";
const PATCH_SOURCE_SHA: &str = "9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab";
const PATCH_TARGET_SHA: &str = "659849ee82da005b08ffa9687311998063fb2bc1abed9367b04e894494cff7e6";
const PATCH_FIRST_ZLIB: &[u8] = &[
    0x78, 0xda, 0x4b, 0x4c, 0x72, 0x76, 0x01, 0x00, 0x03, 0x78, 0x01, 0x4b,
];
const PATCH_LAST_ZLIB: &[u8] = &[0x78, 0xda, 0x53, 0x04, 0x00, 0x00, 0x22, 0x00, 0x22];

fn valid_patch_bytes() -> Vec<u8> {
    let header = format!(
        "{{\"chunk_size\":4,\"format\":\"medicine-chunk-v1\",\"source_sha256\":\"{PATCH_SOURCE_SHA}\",\"source_size_bytes\":8,\"target_sha256\":\"{PATCH_TARGET_SHA}\",\"target_size_bytes\":9}}"
    );
    let mut bytes = Vec::from(&b"MEDPATCH1"[..]);
    bytes.extend_from_slice(&(header.len() as u32).to_be_bytes());
    bytes.extend_from_slice(header.as_bytes());
    for (index, raw_length, compressed) in [
        (0u64, 4u32, PATCH_FIRST_ZLIB),
        (2u64, 1u32, PATCH_LAST_ZLIB),
    ] {
        bytes.extend_from_slice(&index.to_be_bytes());
        bytes.extend_from_slice(&raw_length.to_be_bytes());
        bytes.extend_from_slice(&(compressed.len() as u32).to_be_bytes());
        bytes.extend_from_slice(compressed);
    }
    bytes
}

fn legacy_state_bytes(version: &ReferenceVersion) -> Vec<u8> {
    let mut output = Vec::new();
    put_utf(&mut output, "MEDREFSTATE1");
    output.extend_from_slice(&version.release_sequence.to_be_bytes());
    output.push(1);
    put_utf(&mut output, &version.dataset_id);
    put_utf(&mut output, &version.sha256);
    output.extend_from_slice(&version.size_bytes.to_be_bytes());
    put_utf(&mut output, "10");
    output.extend_from_slice(&version.release_sequence.to_be_bytes());
    output.push(0);
    output.push(0);
    output
}

#[test]
fn reference_state_command_decodes_v3_and_reports_legacy_format() {
    let state_path = common::temp_sqlite_path("cli-reference-state");
    let version = ReferenceVersion {
        dataset_id: format!("sha256:{}", "a".repeat(64)),
        sha256: "b".repeat(64),
        size_bytes: 123,
        contract_major: 1,
        release_sequence: 7,
    };
    let state = ReferenceStoreState {
        active: Some(version.clone()),
        highest_activated_sequence: 7,
        highest_seen_root_sequence: 8,
        highest_seen_root_hash: Some("c".repeat(64)),
        ..ReferenceStoreState::default()
    };
    fs::write(
        &state_path,
        ReferenceStateCodec::encode(&state).expect("encode v3 state"),
    )
    .expect("write v3 state");

    let v3 = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run reference-state v3");
    assert!(
        v3.status.success(),
        "{}",
        String::from_utf8_lossy(&v3.stderr)
    );
    let value: Value = serde_json::from_slice(&v3.stdout).expect("state json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["format"], "MEDREFSTATE3");
    assert_eq!(value["body"]["legacy"], false);
    assert_eq!(value["body"]["state"]["active"]["releaseSequence"], 7);
    assert_eq!(value["body"]["state"]["highestSeenRootSequence"], 8);

    fs::write(&state_path, legacy_state_bytes(&version)).expect("write v1 state");
    let v1 = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run reference-state v1");
    assert!(
        v1.status.success(),
        "{}",
        String::from_utf8_lossy(&v1.stderr)
    );
    let value: Value = serde_json::from_slice(&v1.stdout).expect("legacy state json");
    assert_eq!(value["body"]["format"], "MEDREFSTATE1");
    assert_eq!(value["body"]["legacy"], true);
    assert_eq!(value["body"]["state"]["active"]["releaseSequence"], 7);

    fs::remove_file(state_path).ok();
}

#[test]
fn reference_state_command_fails_closed_for_missing_malformed_or_trailing_state() {
    let state_path = common::temp_sqlite_path("cli-reference-state-invalid");
    fs::remove_file(&state_path).expect("remove reserved missing path");
    let missing = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run missing reference-state");
    assert!(!missing.status.success());
    assert!(String::from_utf8_lossy(&missing.stderr).contains("reference state"));

    fs::write(&state_path, b"not-a-reference-state").expect("write malformed state");
    let malformed = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run malformed reference-state");
    assert!(!malformed.status.success());
    assert!(String::from_utf8_lossy(&malformed.stderr).contains("reference state"));

    let valid = ReferenceStateCodec::encode(&ReferenceStoreState::default()).expect("encode state");
    let mut trailing = valid;
    trailing.push(0xff);
    fs::write(&state_path, trailing).expect("write trailing state");
    let trailing = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run trailing reference-state");
    assert!(!trailing.status.success());
    assert!(String::from_utf8_lossy(&trailing.stderr).contains("trailing reference state data"));
    fs::remove_file(state_path).ok();
}

#[test]
fn reference_apply_full_uses_shared_atomic_core_and_structured_observer_events() {
    let archive = common::temp_sqlite_path("cli-reference-full-gzip");
    let destination = common::temp_sqlite_path("cli-reference-full-destination");
    let target = b"portable-reference-full-target";
    fs::write(&archive, encode_gzip(target)).expect("write gzip fixture");
    fs::write(&destination, b"old-reference").expect("write old destination");
    let target_sha = sha256_hex(target);

    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "full",
            "--artifact",
            archive.to_str().expect("archive path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            &target.len().to_string(),
            "--target-sha256",
            &target_sha,
            "--json",
        ])
        .output()
        .expect("run full reference-apply");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("apply json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["kind"], "full");
    assert_eq!(value["body"]["target_size_bytes"], target.len() as u64);
    assert_eq!(value["body"]["target_sha256"], target_sha);
    assert_eq!(
        fs::read(&destination).expect("read rebuilt destination"),
        target
    );

    let events: Vec<Value> = String::from_utf8_lossy(&output.stderr)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("observer stderr JSON"))
        .collect();
    assert!(events.iter().any(|event| event["event"] == "progress"));
    assert!(events.iter().any(|event| event["event"] == "checkpoint"));
    assert!(
        events.len() <= 16,
        "observer must stay bounded for a tiny artifact: {events:?}"
    );

    fs::remove_file(archive).ok();
    fs::remove_file(destination).ok();
}

#[test]
fn reference_apply_patch_requires_source_and_checks_signed_target_before_replacement() {
    let artifact = common::temp_sqlite_path("cli-reference-patch");
    let source = common::temp_sqlite_path("cli-reference-patch-source");
    let destination = common::temp_sqlite_path("cli-reference-patch-destination");
    fs::write(&artifact, valid_patch_bytes()).expect("write patch fixture");
    fs::write(&source, PATCH_SOURCE).expect("write patch source");
    fs::write(&destination, b"old-reference").expect("write old destination");

    let missing_source = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            PATCH_TARGET_SHA,
            "--json",
        ])
        .output()
        .expect("run patch without source");
    assert!(!missing_source.status.success());
    assert!(String::from_utf8_lossy(&missing_source.stderr).contains("--source"));
    assert_eq!(
        fs::read(&destination).expect("destination after usage failure"),
        b"old-reference"
    );

    let applied = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--source",
            source.to_str().expect("source path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            PATCH_TARGET_SHA,
            "--json",
        ])
        .output()
        .expect("run valid patch apply");
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    let value: Value = serde_json::from_slice(&applied.stdout).expect("patch apply json");
    assert_eq!(value["body"]["kind"], "patch");
    assert_eq!(value["body"]["target_sha256"], PATCH_TARGET_SHA);
    assert_eq!(
        fs::read(&destination).expect("read patch destination"),
        PATCH_TARGET
    );

    fs::write(&destination, b"old-reference").expect("restore old destination");
    let mismatch = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--source",
            source.to_str().expect("source path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            &"d".repeat(64),
            "--json",
        ])
        .output()
        .expect("run patch signed-target mismatch");
    assert!(!mismatch.status.success());
    assert!(String::from_utf8_lossy(&mismatch.stderr).contains("signed target"));
    assert_eq!(
        fs::read(&destination).expect("destination after signed-target mismatch"),
        b"old-reference"
    );

    fs::remove_file(artifact).ok();
    fs::remove_file(source).ok();
    fs::remove_file(destination).ok();
}
