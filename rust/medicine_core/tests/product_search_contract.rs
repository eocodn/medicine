mod common;

use medicine_core::{AccessClass, MedicineEngine};
use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn temp_reference_db(label: &str) -> PathBuf {
    let path = common::temp_sqlite_path(label);
    let con = Connection::open(&path).expect("create reference fixture");
    con.execute_batch(
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
         INSERT INTO products(
             item_seq, product_name, manufacturer, ingredient_text, dosage_form,
             permit_date, cancel_date, cancel_name, permit_status
         ) VALUES(
             'fixture', 'Fixture medicine', 'Fixture manufacturer', 'fixture', 'tablet',
             '2020-01-01', NULL, NULL, 'active'
         );",
    )
    .expect("create reference fixture schema");
    drop(con);
    path
}

fn response(engine: &MedicineEngine, path: &str) -> Value {
    serde_json::from_str(&engine.request("GET", path, "")).expect("response JSON")
}

#[test]
fn product_search_is_rust_owned_and_reference_scoped() {
    let reference = temp_reference_db("product-search-ownership");
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    assert_eq!(
        engine.request_access("GET", "/api/products?q=fixture"),
        AccessClass::Reference
    );
    assert!(engine.handles_request("GET", "/api/products?q=fixture"));

    let unavailable = MedicineEngine::new(None, None, Some("update_required"));
    assert_eq!(
        unavailable.request_access("GET", "/api/products?q=fixture"),
        AccessClass::Reference
    );
    assert!(unavailable.handles_request("GET", "/api/products?q=fixture"));

    fs::remove_file(reference).ok();
}

#[test]
fn product_search_requires_q_and_validates_integer_limit() {
    let reference = temp_reference_db("product-search-validation");
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    let missing_q = response(&engine, "/api/products");
    assert_eq!(missing_q["status"], 400);
    assert_eq!(missing_q["body"]["detail"], "q is required");

    let blank_q = response(&engine, "/api/products?q=%20%20");
    assert_eq!(blank_q["status"], 400);
    assert_eq!(blank_q["body"]["detail"], "q is required");

    let invalid_limit = response(&engine, "/api/products?q=fixture&limit=abc");
    assert_eq!(invalid_limit["status"], 400);
    assert_eq!(invalid_limit["body"]["detail"], "limit must be an integer");

    for limit in ["0", "101"] {
        let out_of_range = response(&engine, &format!("/api/products?q=fixture&limit={limit}"));
        assert_eq!(out_of_range["status"], 400);
        assert_eq!(
            out_of_range["body"]["detail"],
            "limit must be between 1 and 100"
        );
    }

    fs::remove_file(reference).ok();
}

#[test]
fn product_search_parses_include_inactive_boolean_values() {
    let reference = temp_reference_db("product-search-bool");
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    for raw in ["1", "true", "yes", "on", "0", "false", "no", "off"] {
        let parsed = response(
            &engine,
            &format!("/api/products?q=fixture&include_inactive={raw}"),
        );
        assert_eq!(parsed["status"], 503, "boolean form {raw} should parse");
        assert_eq!(
            parsed["body"]["detail"],
            "product search engine is not implemented"
        );
    }

    let invalid = response(&engine, "/api/products?q=fixture&include_inactive=maybe");
    assert_eq!(invalid["status"], 400);
    assert_eq!(
        invalid["body"]["detail"],
        "invalid boolean query parameter: include_inactive"
    );

    fs::remove_file(reference).ok();
}

#[test]
fn product_search_matches_python_query_decoding_and_integer_forms() {
    let reference = temp_reference_db("product-search-python-query");
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    for path in [
        "/api/products?q=%ZZ",
        "/api/products?q=%C3",
        "/api/products?q=%FF",
        "/api/products?q=%",
        "/api/products?q=fixture&limit=1_0",
        "/api/products?q=fixture&limit=%EF%BC%91",
        "/api/products?q=fixture&limit=%D9%A1",
    ] {
        let parsed = response(&engine, path);
        assert_eq!(parsed["status"], 503, "{path}: {parsed}");
    }

    let huge = response(
        &engine,
        "/api/products?q=fixture&limit=999999999999999999999999999999",
    );
    assert_eq!(huge["status"], 400, "{huge}");
    assert_eq!(huge["body"]["detail"], "limit must be between 1 and 100");

    fs::remove_file(reference).ok();
}

#[test]
fn valid_search_keeps_the_intentional_unavailable_engine_envelope() {
    let reference = temp_reference_db("product-search-unavailable");
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    let result = response(&engine, "/api/products?q=fixture&limit=30");
    assert_eq!(result["status"], 503);
    assert_eq!(
        result["body"]["detail"],
        "product search engine is not implemented"
    );

    fs::remove_file(reference).ok();
}

#[test]
fn retired_reference_takes_precedence_with_update_required_envelope() {
    let reference = temp_reference_db("product-search-retired");
    let mut engine = MedicineEngine::new(Some(reference.as_path()), None, None);
    engine
        .set_reference_available(false, Some("retired"))
        .expect("retire reference");

    let result = response(&engine, "/api/products");
    assert_eq!(result["status"], 503);
    assert_eq!(
        result["body"]["detail"],
        "reference data unavailable; app update required"
    );
    assert_eq!(result["body"]["reference_status"], "retired");

    fs::remove_file(reference).ok();
}
