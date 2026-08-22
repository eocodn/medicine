mod common;

use medicine_core::MedicineEngine;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

fn temp_path(label: &str) -> PathBuf {
    common::temp_sqlite_path(&format!("create-{label}"))
}

fn temp_reference_db() -> PathBuf {
    let path = temp_path("reference");
    let con = Connection::open(&path).expect("create reference fixture");
    con.execute_batch(
        r#"
        CREATE TABLE canonical_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE source_snapshots(
            dataset_key TEXT PRIMARY KEY,source_family TEXT NOT NULL,sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,fetched_at TEXT,effective_date TEXT
        );
        CREATE TABLE products(
            item_seq TEXT PRIMARY KEY,product_name TEXT NOT NULL,manufacturer TEXT,
            ingredient_text TEXT,dosage_form TEXT,permit_date TEXT,cancel_date TEXT,
            cancel_name TEXT,permit_status TEXT NOT NULL
        );
        CREATE TABLE product_identifiers(item_seq TEXT,system TEXT,value TEXT);
        CREATE TABLE product_rules(
            id INTEGER PRIMARY KEY,source_dataset_key TEXT,source_row INTEGER,category TEXT,
            item_seq TEXT,ingredient_code TEXT,ingredient_name TEXT,ingredient_name_en TEXT,
            paired_item_seq TEXT,paired_ingredient_code TEXT,paired_ingredient_name TEXT,
            paired_ingredient_name_en TEXT,effect_name TEXT,dosage_form TEXT,details TEXT,
            notification_date TEXT,change_date TEXT
        );
        CREATE TABLE product_flags(
            source_dataset_key TEXT,source_row INTEGER,flag_ordinal INTEGER,item_seq TEXT,
            category TEXT,flag_code TEXT,flag_name TEXT,ingredient_name TEXT,dosage_form TEXT,
            details TEXT,change_date TEXT
        );
        CREATE TABLE ingredient_rules(
            id INTEGER PRIMARY KEY,source_dataset_key TEXT,source_row INTEGER,category TEXT,
            sequence_text TEXT,ingredient_name TEXT,ingredient_name_ko TEXT,
            paired_ingredient_name TEXT,rule_value TEXT,dosage_form TEXT,note TEXT,
            qualifier_note TEXT,details TEXT
        );
        CREATE TABLE dose_criteria(
            criterion_rule_id INTEGER PRIMARY KEY,maximum_daily_amount TEXT,
            maximum_daily_unit TEXT,parse_status TEXT,parse_reason TEXT
        );
        CREATE TABLE product_criterion_links(
            product_rule_id INTEGER,criterion_rule_id INTEGER,match_method TEXT,pair_orientation TEXT
        );
        CREATE TABLE reference_semantic_expectations(
            criterion_rule_id INTEGER PRIMARY KEY,expected_fact_count INTEGER NOT NULL
        );
        CREATE TABLE reference_criterion_semantics(
            criterion_rule_id INTEGER,ordinal INTEGER,semantic_role TEXT,evaluation_mode TEXT,
            evaluator_kind TEXT,fallback_action TEXT,qualifier_type TEXT,display_text TEXT,
            structured_payload_json TEXT,source_remark TEXT
        );
        CREATE VIEW product_rule_criteria AS
        SELECT
            i.id AS criterion_rule_id,r.source_dataset_key AS product_source_dataset_key,
            r.source_row AS product_source_row,i.source_dataset_key AS criterion_source_dataset_key,
            i.source_row AS criterion_source_row,r.category,r.item_seq,r.ingredient_code,
            r.ingredient_name,r.ingredient_name_en,r.paired_item_seq,r.paired_ingredient_code,
            r.paired_ingredient_name,r.paired_ingredient_name_en,r.effect_name,
            r.dosage_form AS product_dosage_form,r.details AS product_details,
            i.sequence_text AS criterion_sequence_text,i.ingredient_name AS criterion_ingredient_name,
            i.ingredient_name_ko AS criterion_ingredient_name_ko,
            i.paired_ingredient_name AS criterion_paired_ingredient_name,
            i.rule_value AS criterion_rule_value,i.dosage_form AS criterion_dosage_form,
            i.note AS criterion_note,i.qualifier_note AS criterion_qualifier_note,
            i.details AS criterion_details,d.maximum_daily_amount AS criterion_maximum_daily_amount,
            d.maximum_daily_unit AS criterion_maximum_daily_unit,d.parse_status AS criterion_dose_parse_status,
            d.parse_reason AS criterion_dose_parse_reason,l.match_method,l.pair_orientation
        FROM product_criterion_links l
        JOIN product_rules r ON r.id=l.product_rule_id
        JOIN ingredient_rules i ON i.id=l.criterion_rule_id
        LEFT JOIN dose_criteria d ON d.criterion_rule_id=i.id;

        INSERT INTO canonical_meta(key,value) VALUES
            ('schema_version','10'),('build_stage','complete'),
            ('built_at','2026-08-22T09:00:00+09:00');
        INSERT INTO source_snapshots(dataset_key,source_family,sha256,row_count)
        VALUES('fixture','mfds_permit_api','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',2);
        INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_text,dosage_form,
            permit_date,cancel_date,cancel_name,permit_status
        ) VALUES
            ('SAFE','안전약','제약','SafeDrug','정제','2020-01-01',NULL,'정상','active'),
            ('INACTIVE','취소약','제약','OldDrug','정제','2020-01-01','2026-01-01','취소','inactive');
        "#,
    )
    .expect("create reference schema");
    drop(con);
    path
}

fn temp_personal_db() -> PathBuf {
    let path = temp_path("personal");
    let con = Connection::open(&path).expect("create personal fixture");
    con.execute_batch(
        r#"
        CREATE TABLE people(
            id TEXT PRIMARY KEY,name TEXT NOT NULL,birth_date TEXT NOT NULL,sex TEXT NOT NULL,
            pregnancy_status TEXT NOT NULL,lactation_status TEXT NOT NULL,notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE medications(
            id TEXT PRIMARY KEY,person_id TEXT NOT NULL,catalog_item_seq TEXT,product_code TEXT,
            product_name TEXT NOT NULL,ingredient_code TEXT,ingredient_name TEXT,manufacturer TEXT,
            catalog_source TEXT,dosage_text TEXT,dose_amount REAL,dose_unit TEXT,
            frequency_per_day INTEGER,meal_relation TEXT,administration_route TEXT,
            as_needed INTEGER NOT NULL DEFAULT 0,prn_max_per_day INTEGER,prescription_days INTEGER,
            long_term INTEGER NOT NULL DEFAULT 0,start_date TEXT,end_date TEXT,
            active INTEGER NOT NULL DEFAULT 1,source TEXT NOT NULL DEFAULT 'dur_search',
            stopped_at TEXT,revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE medication_schedules(
            id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,time_of_day TEXT NOT NULL,dose_text TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE medication_revisions(
            medication_id TEXT NOT NULL,revision INTEGER NOT NULL,action TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,assessment_json TEXT,acknowledged INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,payload_hash TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id,revision)
        );
        CREATE TABLE medication_requests(
            request_id TEXT PRIMARY KEY,person_id TEXT NOT NULL,payload_hash TEXT NOT NULL,
            medication_id TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status)
        VALUES
            ('adult','성인','1990-01-01','male','not_applicable','not_applicable'),
            ('child','소아','2015-01-01','male','not_applicable','not_applicable');
        "#,
    )
    .expect("create personal schema");
    drop(con);
    path
}

fn engine(reference: &Path, personal: &Path) -> MedicineEngine {
    MedicineEngine::new(Some(reference), Some(personal), None)
}

fn create(engine: &MedicineEngine, person_id: &str, body: Value) -> Value {
    serde_json::from_str(&engine.request(
        "POST",
        &format!("/api/people/{person_id}/medications"),
        &body.to_string(),
    ))
    .expect("create response json")
}

fn row_count(path: &Path, table: &str) -> i64 {
    let con = Connection::open(path).expect("open count fixture");
    con.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
        row.get(0)
    })
    .expect("row count")
}

#[test]
fn create_route_is_atomic_and_request_id_is_idempotent() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let engine = engine(&reference, &personal);
    assert!(engine.handles_request("POST", "/api/people/adult/medications"));
    assert!(engine.handles_request("PATCH", "/api/medications/example"));

    let body = json!({
        "product_ref":"SAFE",
        "dose_amount":1,
        "dose_unit":"정",
        "frequency_per_day":2,
        "schedule_times":["08:00","20:00"],
        "administration_route":"oral",
        "prescription_days":7,
        "start_date":"2026-08-20",
        "request_id":"safe-create-1"
    });
    let first = create(&engine, "adult", body.clone());
    assert_eq!(first["status"], 201);
    assert_eq!(first["body"]["catalog_item_seq"], "SAFE");
    assert_eq!(first["body"]["source"], "catalog_search");
    assert_eq!(first["body"]["revision"], 1);
    assert_eq!(first["body"]["schedules"].as_array().map(Vec::len), Some(2));
    assert_eq!(first["body"]["assessment"]["requires_review"], false);
    assert_eq!(row_count(&personal, "medications"), 1);
    assert_eq!(row_count(&personal, "medication_schedules"), 2);
    assert_eq!(row_count(&personal, "medication_revisions"), 1);
    assert_eq!(row_count(&personal, "medication_requests"), 1);

    let retry = create(&engine, "adult", body.clone());
    assert_eq!(retry["status"], 201);
    assert_eq!(retry["body"]["id"], first["body"]["id"]);
    assert_eq!(row_count(&personal, "medications"), 1);

    let conflict = create(
        &engine,
        "adult",
        json!({"product_ref":"SAFE","dose_amount":2,"dose_unit":"정","prescription_days":7,
               "start_date":"2026-08-20","request_id":"safe-create-1"}),
    );
    assert_eq!(conflict["status"], 409);
    assert_eq!(
        conflict["body"]["detail"],
        "request_id was already used with a different prescription payload"
    );
    assert_eq!(row_count(&personal, "medications"), 1);

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn create_requires_exact_warning_acknowledgement_before_any_write() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let engine = engine(&reference, &personal);
    let body = json!({
        "product_ref":"SAFE","dose_amount":1,"dose_unit":"정",
        "prescription_days":7,"start_date":"2026-08-20","request_id":"child-create-1"
    });

    let blocked = create(&engine, "child", body.clone());
    assert_eq!(blocked["status"], 409);
    assert_eq!(blocked["body"]["confirmation_required"], true);
    assert_eq!(blocked["body"]["request_id"], "child-create-1");
    assert_eq!(blocked["body"]["assessment"]["requires_review"], true);
    let warning_token = blocked["body"]["warning_token"]
        .as_str()
        .expect("warning token")
        .to_owned();
    assert_eq!(row_count(&personal, "medications"), 0);
    assert_eq!(row_count(&personal, "medication_revisions"), 0);
    assert_eq!(row_count(&personal, "medication_requests"), 0);

    let wrong = create(
        &engine,
        "child",
        json!({
            "product_ref":"SAFE","dose_amount":1,"dose_unit":"정",
            "prescription_days":7,"start_date":"2026-08-20","request_id":"child-create-1",
            "acknowledge_warnings":true,"warning_token":"wrong"
        }),
    );
    assert_eq!(wrong["status"], 409);
    assert_eq!(row_count(&personal, "medications"), 0);

    let padded = create(
        &engine,
        "child",
        json!({
            "product_ref":"SAFE","dose_amount":1,"dose_unit":"정",
            "prescription_days":7,"start_date":"2026-08-20","request_id":"child-create-1",
            "acknowledge_warnings":true,"warning_token":format!(" {warning_token} ")
        }),
    );
    assert_eq!(padded["status"], 409);
    assert_eq!(row_count(&personal, "medications"), 0);

    let accepted = create(
        &engine,
        "child",
        json!({
            "product_ref":"SAFE","dose_amount":1,"dose_unit":"정",
            "prescription_days":7,"start_date":"2026-08-20","request_id":"child-create-1",
            "acknowledge_warnings":true,"warning_token":warning_token
        }),
    );
    assert_eq!(accepted["status"], 201);
    assert_eq!(accepted["body"]["assessment"]["acknowledged"], true);
    assert_eq!(row_count(&personal, "medications"), 1);
    assert_eq!(row_count(&personal, "medication_revisions"), 1);
    assert_eq!(row_count(&personal, "medication_requests"), 1);

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn create_preserves_manual_product_and_validation_contracts() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let engine = engine(&reference, &personal);

    let missing_bound = create(&engine, "adult", json!({"product_ref":"SAFE"}));
    assert_eq!(missing_bound["status"], 400);
    assert_eq!(
        missing_bound["body"]["detail"],
        "prescription duration or explicit long_term mode is required"
    );

    let inactive = create(
        &engine,
        "adult",
        json!({"product_ref":"INACTIVE","long_term":true}),
    );
    assert_eq!(inactive["status"], 400);
    assert_eq!(
        inactive["body"]["detail"],
        "inactive permit product cannot be added to the current medication regimen"
    );

    let manual = create(
        &engine,
        "adult",
        json!({"manual_name":" 수기약 ","ingredient_name":"성분","long_term":true,"request_id":"manual-1"}),
    );
    assert_eq!(manual["status"], 409);
    assert_eq!(manual["body"]["confirmation_required"], true);
    assert_eq!(
        manual["body"]["assessment"]["coverage"]["status"],
        "limited"
    );
    let token = manual["body"]["warning_token"]
        .as_str()
        .expect("manual warning token");
    let accepted = create(
        &engine,
        "adult",
        json!({
            "manual_name":" 수기약 ","ingredient_name":"성분","long_term":true,
            "request_id":"manual-1","acknowledge_warnings":true,"warning_token":token
        }),
    );
    assert_eq!(accepted["status"], 201);
    assert_eq!(accepted["body"]["product_name"], "수기약");
    assert_eq!(accepted["body"]["ingredient_name"], "성분");
    assert_eq!(accepted["body"]["catalog_source"], "manual");
    assert_eq!(accepted["body"]["source"], "manual");
    assert!(accepted["body"]["catalog_item_seq"].is_null());

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn create_keeps_product_code_alias_and_reference_disabled_envelope() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let mut engine = engine(&reference, &personal);

    let created = create(
        &engine,
        "adult",
        json!({"product_code":"SAFE","long_term":true,"request_id":"alias-1"}),
    );
    assert_eq!(created["status"], 201);
    assert_eq!(created["body"]["catalog_item_seq"], "SAFE");
    assert_eq!(row_count(&personal, "medications"), 1);

    engine
        .set_reference_available(false, Some("update_required"))
        .expect("disable reference");
    let blocked = create(
        &engine,
        "adult",
        json!({"product_code":"SAFE","long_term":true,"request_id":"alias-2"}),
    );
    assert_eq!(blocked["status"], 503);
    assert_eq!(
        blocked["body"]["detail"],
        "reference data unavailable; app update required"
    );
    assert_eq!(blocked["body"]["reference_status"], "update_required");
    assert_eq!(row_count(&personal, "medications"), 1);

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}
