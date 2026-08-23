mod common;

use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn fixture_db() -> PathBuf {
    let path = common::temp_sqlite_path("product-search-behavior");
    let con = Connection::open(&path).expect("create search fixture");
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

         INSERT INTO products VALUES
           ('P-GABA','메가펜틴캡슐300밀리그램(가바펜틴)','일동제약(주)','Gabapentin','캡슐제','2020-01-01',NULL,NULL,'active'),
           ('P-B12','비타민B12정','한미약품(주)','Cyanocobalamin','정제','2020-01-01',NULL,NULL,'active'),
           ('P-FLU','인플루엔자백신','백신회사','Influenza B/California/12/2015','주사제','2020-01-01',NULL,NULL,'active'),
           ('P-INACTIVE','가바펜틴구제품','일동제약(주)','Gabapentin','정제','2010-01-01','2020-01-01','취하','withdrawn');

         INSERT INTO product_search_documents VALUES
           ('P-GABA','메가펜틴캡슐300밀리그램(가바펜틴)','일동제약(주)',char(10)||'gabapentin'||char(10)||'가바펜틴'||char(10)),
           ('P-B12','비타민b12정','한미약품(주)',char(10)||'cyanocobalamin'||char(10)||'시아노코발라민'||char(10)),
           ('P-FLU','인플루엔자백신','백신회사',char(10)||'influenza b/california/12/2015'||char(10)),
           ('P-INACTIVE','가바펜틴구제품','일동제약(주)',char(10)||'gabapentin'||char(10)||'가바펜틴'||char(10));

         INSERT INTO product_search_fts(rowid,searchable_text)
         SELECT rowid,normalized_product_name||char(10)||normalized_manufacturer||normalized_ingredient_names
         FROM product_search_documents;

         INSERT INTO product_rules VALUES(1,'P-GABA','duration_caution',NULL);
         INSERT INTO product_criterion_links VALUES(1,11);",
    )
    .expect("search fixture schema");
    drop(con);
    path
}

fn get(engine: &MedicineEngine, path: &str) -> Value {
    serde_json::from_str(&engine.request("GET", path, "")).expect("response JSON")
}

#[test]
fn searches_product_manufacturer_and_direct_ingredient_aliases_lexically() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    let ingredient = get(
        &engine,
        "/api/products?q=%EA%B0%80%EB%B0%94%ED%8E%9C%ED%8B%B4",
    );
    assert_eq!(ingredient["status"], 200, "{ingredient}");
    assert_eq!(ingredient["body"]["items"][0]["product_ref"], "P-GABA");
    assert_eq!(
        ingredient["body"]["items"][0]["dur_coverage_status"],
        "complete"
    );

    let manufacturer_and_ingredient = get(
        &engine,
        "/api/products?q=%EC%9D%BC%EB%8F%99+%EA%B0%80%EB%B0%94%ED%8E%9C%ED%8B%B4",
    );
    assert_eq!(manufacturer_and_ingredient["status"], 200);
    assert_eq!(
        manufacturer_and_ingredient["body"]["items"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        manufacturer_and_ingredient["body"]["items"][0]["product_ref"],
        "P-GABA"
    );

    fs::remove_file(reference).ok();
}

#[test]
fn alphanumeric_terms_remain_contiguous_lexical_text() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    let result = get(&engine, "/api/products?q=B12");
    assert_eq!(result["status"], 200, "{result}");
    let refs: Vec<_> = result["body"]["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["product_ref"].as_str().unwrap())
        .collect();
    assert_eq!(refs, vec!["P-B12"]);

    fs::remove_file(reference).ok();
}

#[test]
fn inactive_products_are_filtered_by_default_and_pagination_is_stable() {
    let reference = fixture_db();
    let engine = MedicineEngine::new(Some(reference.as_path()), None, None);

    let active = get(&engine, "/api/products?q=%EA%B0%80&limit=1&offset=0");
    assert_eq!(active["status"], 200);
    assert_eq!(active["body"]["items"][0]["product_ref"], "P-GABA");
    assert_eq!(active["body"]["has_more"], false);

    let first = get(
        &engine,
        "/api/products?q=%EA%B0%80&include_inactive=true&limit=1&offset=0",
    );
    assert_eq!(first["body"]["items"][0]["product_ref"], "P-INACTIVE");
    assert_eq!(first["body"]["has_more"], true);
    assert_eq!(first["body"]["next_offset"], 1);

    let second = get(
        &engine,
        "/api/products?q=%EA%B0%80&include_inactive=true&limit=1&offset=1",
    );
    assert_eq!(second["body"]["items"][0]["product_ref"], "P-GABA");
    assert_eq!(second["body"]["has_more"], false);

    fs::remove_file(reference).ok();
}
