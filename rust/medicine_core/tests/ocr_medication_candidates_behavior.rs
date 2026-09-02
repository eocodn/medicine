mod common;

use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn fixture_db() -> PathBuf {
    let path = common::temp_sqlite_path("ocr-medication-candidates");
    let con = Connection::open(&path).expect("create candidate fixture");
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
         CREATE TABLE product_search_documents(
             item_seq TEXT PRIMARY KEY,
             normalized_product_name TEXT NOT NULL,
             normalized_manufacturer TEXT NOT NULL,
             normalized_ingredient_names TEXT NOT NULL
         );
         CREATE VIRTUAL TABLE product_search_fts USING fts5(
             searchable_text, tokenize='trigram', content=''
         );
         CREATE TABLE product_rules(id INTEGER PRIMARY KEY,item_seq TEXT NOT NULL,category TEXT NOT NULL,effect_name TEXT);
         CREATE TABLE product_criterion_links(product_rule_id INTEGER NOT NULL,criterion_rule_id INTEGER NOT NULL);
         INSERT INTO products VALUES
           ('P1','아세트아미노펜정500밀리그램','제약사',NULL,'정제','2020-01-01',NULL,NULL,'active'),
           ('P2','아세트아미노펜정650밀리그램','제약사',NULL,'정제','2020-01-01',NULL,NULL,'active'),
           ('P3','세티리진정10밀리그램','제약사',NULL,'정제','2020-01-01',NULL,NULL,'active'),
           ('P4','덱스트로메토르판캡슐15밀리그램','제약사',NULL,'캡슐제','2020-01-01',NULL,NULL,'active');
         INSERT INTO product_search_documents
           SELECT item_seq, lower(product_name), '', '' FROM products;
         INSERT INTO product_search_fts(rowid,searchable_text)
           SELECT rowid,normalized_product_name FROM product_search_documents;"
    ).expect("fixture schema");
    drop(con);
    path
}

fn post(engine: &MedicineEngine, queries: Value) -> Value {
    serde_json::from_str(&engine.request(
        "POST",
        "/api/products/ocr-candidates",
        &json!({"queries": queries}).to_string(),
    )).expect("response JSON")
}

#[test]
fn ocr_candidate_search_uses_product_identity_not_generic_context() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);
    let result = post(&engine, json!([
        {"query_id":"q1","text":"아세트아미노펜정 500mg"},
        {"query_id":"q2","text":"아세트아미노펜저 650mg"},
        {"query_id":"q3","text":"덱스트로메토르판캡슐"},
        {"query_id":"q4","text":"본인부담금"},
        {"query_id":"q5","text":"관리약사"},
        {"query_id":"q6","text":"정"}
    ]));
    assert_eq!(result["status"], 200, "{result}");
    let rows = result["body"]["rows"].as_array().expect("rows");
    let names: Vec<_> = rows.iter().map(|row| row["product_query"].as_str().unwrap()).collect();
    assert_eq!(names, vec![
        "아세트아미노펜정500밀리그램",
        "아세트아미노펜정650밀리그램",
        "덱스트로메토르판캡슐15밀리그램",
    ]);
    assert!(rows.iter().all(|row| {
        row.as_object()
            .is_some_and(|value| value.len() == 2 && value.contains_key("row_id") && value.contains_key("product_query"))
    }));
    fs::remove_file(reference).ok();
}

#[test]
fn ocr_candidate_search_is_reference_read_and_rejects_malformed_input() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);
    assert_eq!(engine.request_access("POST", "/api/products/ocr-candidates").as_str(), "reference");
    let malformed: Value = serde_json::from_str(&engine.request(
        "POST", "/api/products/ocr-candidates", r#"{"queries":"bad"}"#,
    )).unwrap();
    assert_eq!(malformed["status"], 400);
    fs::remove_file(reference).ok();
}

#[test]
fn ocr_candidate_search_consumes_overlapping_spans_once_and_keeps_ambiguous_identity_for_review() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);
    let result = post(&engine, json!([
        {"query_id":"long","text":"아세트아미노펜정","node_ids":["a","b"]},
        {"query_id":"left","text":"아세트아미노펜","node_ids":["a"]},
        {"query_id":"right","text":"정","node_ids":["b"]},
        {"query_id":"other","text":"세티리진정 10mg","node_ids":["c"]}
    ]));
    assert_eq!(result["status"], 200, "{result}");
    let rows = result["body"]["rows"].as_array().expect("rows");
    assert_eq!(rows.len(), 2, "{result}");
    assert_eq!(rows[0]["row_id"], "long");
    assert_eq!(rows[0]["product_query"], "아세트아미노펜정");
    assert_eq!(rows[1]["product_query"], "세티리진정10밀리그램");
    fs::remove_file(reference).ok();
}

#[test]
fn ocr_candidate_search_ignores_trailing_regimen_after_strength() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);
    let result = post(&engine, json!([
        {"query_id":"q1","text":"아세트아미노펜정 500mg 1정"}
    ]));
    assert_eq!(result["status"], 200, "{result}");
    assert_eq!(result["body"]["rows"][0]["product_query"], "아세트아미노펜정500밀리그램");
    fs::remove_file(reference).ok();
}
