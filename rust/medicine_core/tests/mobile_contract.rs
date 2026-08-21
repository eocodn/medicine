use medicine_core::{AccessClass, MedicineEngine};
use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_db(name: &str, with_product: bool) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-{name}-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create sqlite fixture");
    con.execute_batch("CREATE TABLE products(item_seq TEXT PRIMARY KEY);")
        .expect("create products table");
    if with_product {
        con.execute("INSERT INTO products(item_seq) VALUES ('fixture')", [])
            .expect("insert product");
    }
    drop(con);
    path
}

#[test]
fn request_policy_matches_existing_mobile_contract() {
    let engine = MedicineEngine::new(None, None);

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

    let mut available = MedicineEngine::new(Some(populated.as_path()), None);
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

    let empty_engine = MedicineEngine::new(Some(empty.as_path()), None);
    let response: Value = serde_json::from_str(&empty_engine.request("GET", "/api/health", ""))
        .expect("empty health response json");
    assert_eq!(response["body"]["full_catalog"], false);

    fs::remove_file(populated).ok();
    fs::remove_file(empty).ok();
}

#[test]
fn only_health_is_owned_by_rust_in_the_first_slice() {
    let engine = MedicineEngine::new(None, None);
    assert!(engine.handles_request("GET", "/api/health"));
    assert!(!engine.handles_request("GET", "/api/people"));
    assert!(!engine.handles_request("POST", "/api/dose-instances/example"));
}
