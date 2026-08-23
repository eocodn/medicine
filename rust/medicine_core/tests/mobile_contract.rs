mod common;

use medicine_core::{AccessClass, MedicineEngine};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn temp_db(name: &str, with_product: bool) -> PathBuf {
    let path = common::temp_sqlite_path(name);
    let con = Connection::open(&path).expect("create sqlite fixture");
    con.execute_batch(
        "CREATE TABLE products(item_seq TEXT PRIMARY KEY);
         CREATE TABLE product_search_documents(
             item_seq TEXT PRIMARY KEY,
             normalized_product_name TEXT NOT NULL,
             normalized_manufacturer TEXT NOT NULL,
             normalized_ingredient_names TEXT NOT NULL
         );
         CREATE VIRTUAL TABLE product_search_fts USING fts5(
             searchable_text, tokenize='trigram', content=''
         );",
    )
    .expect("create products table");
    if with_product {
        con.execute("INSERT INTO products(item_seq) VALUES ('fixture')", [])
            .expect("insert product");
        con.execute(
            "INSERT INTO product_search_documents VALUES ('fixture','fixture','','')",
            [],
        )
        .expect("insert search document");
        con.execute(
            "INSERT INTO product_search_fts(rowid,searchable_text)
             SELECT rowid,normalized_product_name FROM product_search_documents",
            [],
        )
        .expect("insert search accelerator row");
    }
    drop(con);
    path
}

fn legacy_temp_db(name: &str) -> PathBuf {
    let path = common::temp_sqlite_path(name);
    let con = Connection::open(&path).expect("create legacy sqlite fixture");
    con.execute_batch(
        "CREATE TABLE products(item_seq TEXT PRIMARY KEY);
         INSERT INTO products(item_seq) VALUES ('fixture');",
    )
    .expect("create legacy products table");
    drop(con);
    path
}

fn temp_personal_db(name: &str) -> PathBuf {
    let path = common::temp_sqlite_path(name);
    let con = Connection::open(&path).expect("create personal sqlite fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE people(
             id TEXT PRIMARY KEY,
             name TEXT NOT NULL,
             birth_date TEXT NOT NULL,
             sex TEXT NOT NULL,
             pregnancy_status TEXT NOT NULL,
             lactation_status TEXT NOT NULL DEFAULT 'unknown',
             notes TEXT,
             created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
         );
         CREATE TABLE medications(
             id TEXT PRIMARY KEY,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE
         );
         CREATE TABLE medication_schedules(
             id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE CASCADE
         );
         CREATE TABLE dose_logs(
             id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE
         );
         CREATE TABLE dose_instances(
             id TEXT PRIMARY KEY,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE
         );
         CREATE TABLE medication_revisions(
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT,
             revision INTEGER NOT NULL,
             PRIMARY KEY(medication_id, revision)
         );
         CREATE TABLE medication_requests(
             request_id TEXT PRIMARY KEY,
             person_id TEXT NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
             medication_id TEXT NOT NULL REFERENCES medications(id) ON DELETE RESTRICT
         );",
    )
    .expect("create personal tables");
    drop(con);
    path
}

#[test]
fn request_policy_matches_existing_mobile_contract() {
    let engine = MedicineEngine::new(None, None, None);

    assert_eq!(
        engine.request_access("GET", "/api/health"),
        AccessClass::Reference
    );
    assert_eq!(
        engine.request_access("GET", "/api/products?q=test"),
        AccessClass::Reference
    );
    assert_eq!(
        engine.request_access("GET", "/api/people"),
        AccessClass::PersonalRead
    );
    assert_eq!(
        engine.request_access("GET", "/api/medications/example/history"),
        AccessClass::PersonalRead
    );
    assert_eq!(
        engine.request_access("GET", "/api/people/example/medications?date=2026-08-20"),
        AccessClass::PersonalRead
    );
    assert_eq!(
        engine.request_access("POST", "/api/people/example/medications/preview"),
        AccessClass::PersonalRead
    );
    assert_eq!(
        engine.request_access("POST", "/api/people/example/medications"),
        AccessClass::PersonalWrite
    );
    assert_eq!(
        engine.request_access("PATCH", "/api/medications/example"),
        AccessClass::PersonalWrite
    );
    assert_eq!(
        engine.request_access("POST", "/api/dose-instances/example"),
        AccessClass::PersonalWrite
    );
}

#[test]
fn health_reports_reference_and_catalog_state() {
    let populated = temp_db("populated", true);
    let empty = temp_db("empty", false);
    let legacy = legacy_temp_db("legacy");

    let mut available = MedicineEngine::new(Some(populated.as_path()), None, None);
    let response: Value = serde_json::from_str(&available.request("GET", "/api/health", ""))
        .expect("health response json");
    assert_eq!(response["status"], 200);
    assert_eq!(response["body"]["ok"], true);
    assert_eq!(response["body"]["full_catalog"], true);
    assert_eq!(response["body"]["reference_available"], true);
    assert!(response["body"]["reference_status"].is_null());

    available
        .set_reference_available(false, Some("update_required"))
        .expect("disable reference");
    let response: Value = serde_json::from_str(&available.request("GET", "/api/health", ""))
        .expect("disabled health response json");
    assert_eq!(response["body"]["full_catalog"], false);
    assert_eq!(response["body"]["reference_available"], false);
    assert_eq!(response["body"]["reference_status"], "update_required");

    let empty_engine = MedicineEngine::new(Some(empty.as_path()), None, None);
    let response: Value = serde_json::from_str(&empty_engine.request("GET", "/api/health", ""))
        .expect("empty health response json");
    assert_eq!(response["body"]["full_catalog"], false);

    let legacy_engine = MedicineEngine::new(Some(legacy.as_path()), None, None);
    let response: Value = serde_json::from_str(&legacy_engine.request("GET", "/api/health", ""))
        .expect("legacy health response json");
    assert_eq!(response["body"]["reference_available"], true);
    assert_eq!(response["body"]["full_catalog"], false);

    fs::remove_file(populated).ok();
    fs::remove_file(empty).ok();
    fs::remove_file(legacy).ok();
}

#[test]
fn migrated_runtime_routes_are_owned_by_rust() {
    let engine = MedicineEngine::new(None, None, None);
    assert!(engine.handles_request("GET", "/api/health"));
    assert!(engine.handles_request("GET", "/api/products?q=example"));
    assert!(engine.handles_request("GET", "/api/people"));
    assert!(engine.handles_request("POST", "/api/people"));
    assert!(engine.handles_request("PATCH", "/api/people/example"));
    assert!(engine.handles_request("DELETE", "/api/people/example"));
    assert!(engine.handles_request("GET", "/api/people/example/daily-plan?date=2026-08-20"));
    assert!(engine.handles_request("GET", "/api/people/example/dashboard?date=2026-08-20"));
    assert!(engine.handles_request("GET", "/api/people/example/medications?date=2026-08-20"));
    assert!(engine.handles_request("GET", "/api/medications/example/history"));
    assert!(engine.handles_request("DELETE", "/api/medications/example?expected_revision=1"));
    assert!(engine.handles_request("POST", "/api/people/example/medications/preview"));
    assert!(engine.handles_request("POST", "/api/people/example/medications"));
    assert!(engine.handles_request("PATCH", "/api/medications/example"));
    assert!(engine.handles_request("POST", "/api/medications/example/prn-intakes"));
    assert!(engine.handles_request("POST", "/api/dose-instances/example"));
    assert!(engine.handles_request("DELETE", "/api/dose-instances/example/completion"));
}

#[test]
fn rust_people_routes_preserve_profile_contract_and_child_cleanup() {
    let personal = temp_personal_db("people");
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    assert!(engine.handles_request("GET", "/api/people"));
    assert!(engine.handles_request("POST", "/api/people"));

    let created: Value = serde_json::from_str(
        &engine.request(
            "POST",
            "/api/people",
            &json!({
                "name": "  테스트  ",
                "birth_date": "1990-01-01",
                "sex": "male",
                "pregnancy_status": "pregnant",
                "lactation_status": "breastfeeding",
                "notes": "memo"
            })
            .to_string(),
        ),
    )
    .expect("create response json");
    assert_eq!(created["status"], 201);
    assert_eq!(created["body"]["name"], "테스트");
    assert_eq!(created["body"]["pregnancy_status"], "not_applicable");
    assert_eq!(created["body"]["lactation_status"], "not_applicable");
    assert_eq!(created["body"]["profile_needs_review"], false);
    assert!(created["body"]["age"].as_u64().is_some());
    let person_id = created["body"]["id"]
        .as_str()
        .expect("created person id")
        .to_owned();

    let con = Connection::open(&personal).expect("open personal fixture");
    con.execute(
        "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status)
         VALUES('legacy','legacy','2000-01-01','unknown','unknown','unknown')",
        [],
    )
    .expect("insert legacy profile");
    drop(con);

    let listed: Value = serde_json::from_str(&engine.request("GET", "/api/people", ""))
        .expect("people list response json");
    assert_eq!(listed["status"], 200);
    assert_eq!(listed["body"].as_array().expect("people array").len(), 2);
    assert_eq!(listed["body"][0]["id"], person_id);
    assert_eq!(listed["body"][1]["profile_needs_review"], true);

    let updated: Value = serde_json::from_str(
        &engine.request(
            "PATCH",
            &format!("/api/people/{person_id}"),
            &json!({
                "name": "수정",
                "birth_date": "1991-02-03",
                "sex": "female",
                "pregnancy_status": "not_pregnant",
                "lactation_status": "breastfeeding",
                "notes": null
            })
            .to_string(),
        ),
    )
    .expect("update response json");
    assert_eq!(updated["status"], 200);
    assert_eq!(updated["body"]["sex"], "female");
    assert_eq!(updated["body"]["lactation_status"], "breastfeeding");
    assert!(updated["body"]["notes"].is_null());

    let con = Connection::open(&personal).expect("open personal children fixture");
    con.execute("PRAGMA foreign_keys=ON", [])
        .expect("foreign keys on");
    con.execute(
        "INSERT INTO medications(id,person_id) VALUES('m1',?)",
        [&person_id],
    )
    .expect("insert medication");
    con.execute(
        "INSERT INTO medication_schedules(id,medication_id) VALUES('s1','m1')",
        [],
    )
    .expect("insert schedule");
    con.execute(
        "INSERT INTO medication_revisions(medication_id,revision) VALUES('m1',1)",
        [],
    )
    .expect("insert revision");
    con.execute(
        "INSERT INTO medication_requests(request_id,person_id,medication_id) VALUES('r1',?,'m1')",
        [&person_id],
    )
    .expect("insert request");
    con.execute(
        "INSERT INTO dose_instances(id,medication_id,person_id) VALUES('i1','m1',?)",
        [&person_id],
    )
    .expect("insert dose instance");
    con.execute(
        "INSERT INTO dose_logs(id,medication_id,person_id) VALUES('l1','m1',?)",
        [&person_id],
    )
    .expect("insert dose log");
    drop(con);

    let deleted: Value =
        serde_json::from_str(&engine.request("DELETE", &format!("/api/people/{person_id}"), ""))
            .expect("delete response json");
    assert_eq!(deleted["status"], 200);
    assert_eq!(deleted["body"]["id"], person_id);
    assert_eq!(deleted["body"]["deleted"], true);

    let con = Connection::open(&personal).expect("verify person cleanup");
    for table in [
        "dose_logs",
        "dose_instances",
        "medication_requests",
        "medication_schedules",
        "medication_revisions",
        "medications",
    ] {
        let count: i64 = con
            .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .expect("child row count");
        assert_eq!(count, 0, "{table} should be empty after person deletion");
    }
    drop(con);

    let missing: Value =
        serde_json::from_str(&engine.request("DELETE", &format!("/api/people/{person_id}"), ""))
            .expect("missing person response json");
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "person not found");

    fs::remove_file(personal).ok();
}

#[test]
fn rust_people_routes_keep_validation_envelopes() {
    let personal = temp_personal_db("people-validation");
    let engine = MedicineEngine::new(None, Some(personal.as_path()), None);

    let female_missing_status: Value = serde_json::from_str(
        &engine.request(
            "POST",
            "/api/people",
            &json!({
                "name": "여성",
                "birth_date": "1990-01-01",
                "sex": "female"
            })
            .to_string(),
        ),
    )
    .expect("female validation response json");
    assert_eq!(female_missing_status["status"], 400);
    assert_eq!(
        female_missing_status["body"]["detail"],
        "pregnancy_status must be pregnant or not_pregnant for female profiles"
    );

    let unknown: Value = serde_json::from_str(
        &engine.request(
            "POST",
            "/api/people",
            &json!({
                "name": "필드",
                "birth_date": "1990-01-01",
                "sex": "male",
                "extra": true
            })
            .to_string(),
        ),
    )
    .expect("unknown-field response json");
    assert_eq!(unknown["status"], 400);
    assert_eq!(unknown["body"]["detail"], "unknown fields: extra");

    let invalid_birth: Value = serde_json::from_str(&engine.request(
        "POST",
        "/api/people",
        &json!({"name":"날짜","birth_date":"not-a-date","sex":"male"}).to_string(),
    ))
    .expect("birth-date response json");
    assert_eq!(invalid_birth["status"], 400);
    assert_eq!(
        invalid_birth["body"]["detail"],
        "birth_date must be YYYY-MM-DD"
    );

    fs::remove_file(personal).ok();
}
