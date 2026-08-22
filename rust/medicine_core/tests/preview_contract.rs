use medicine_core::MedicineEngine;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_path(label: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("medicine-preview-{label}-{nonce}.sqlite"))
}

fn temp_reference_db() -> PathBuf {
    let path = temp_path("reference");
    let con = Connection::open(&path).expect("create preview reference fixture");
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
            ('OTHER','다른약','제약','OtherDrug','정제','2020-01-01',NULL,'정상','active');
        "#,
    )
    .expect("create preview reference schema");
    drop(con);
    path
}

fn temp_personal_db() -> PathBuf {
    let path = temp_path("personal");
    let con = Connection::open(&path).expect("create preview personal fixture");
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

        INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status)
        VALUES
            ('adult','성인','1990-01-01','male','not_applicable','not_applicable'),
            ('child','소아','2015-01-01','male','not_applicable','not_applicable');
        "#,
    )
    .expect("create preview personal schema");
    drop(con);
    path
}

fn engine(reference: &Path, personal: &Path) -> MedicineEngine {
    MedicineEngine::new(Some(reference), Some(personal), None)
}

fn preview(engine: &MedicineEngine, person_id: &str, body: Value) -> Value {
    serde_json::from_str(&engine.request(
        "POST",
        &format!("/api/people/{person_id}/medications/preview"),
        &body.to_string(),
    ))
    .expect("preview response json")
}

fn insert_duplicate(personal: &PathBuf) {
    let con = Connection::open(personal).expect("open duplicate fixture");
    con.execute(
        "INSERT INTO medications(
            id,person_id,catalog_item_seq,product_code,product_name,ingredient_name,manufacturer,
            catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,meal_relation,
            administration_route,as_needed,prn_max_per_day,prescription_days,long_term,
            start_date,end_date,active,source,revision
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            "existing-safe",
            "adult",
            "SAFE",
            "SAFE",
            "안전약",
            "SafeDrug",
            "제약",
            "canonical",
            "1정",
            1.0,
            "정",
            2,
            "unspecified",
            "oral",
            0,
            Option::<i64>::None,
            7,
            0,
            "2026-08-20",
            "2026-08-26",
            1,
            "canonical",
            1,
        ],
    )
    .expect("insert duplicate medication");
    con.execute(
        "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
         VALUES('s1','existing-safe','08:00','1정'),('s2','existing-safe','20:00','1정')",
        [],
    )
    .expect("insert duplicate schedules");
    con.execute(
        "INSERT INTO medication_revisions(
            medication_id,revision,action,snapshot_json,assessment_json,acknowledged
         ) VALUES('existing-safe',1,'create','{}','{}',0)",
        [],
    )
    .expect("insert duplicate revision");
    drop(con);
}

#[test]
fn preview_route_keeps_validation_and_not_found_envelopes() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let engine = engine(&reference, &personal);

    assert!(engine.handles_request("POST", "/api/people/adult/medications/preview"));
    assert!(!engine.handles_request("POST", "/api/people/adult/medications"));
    assert!(!engine.handles_request("PATCH", "/api/medications/example"));

    let missing_ref = preview(&engine, "adult", json!({"long_term": true}));
    assert_eq!(missing_ref["status"], 400);
    assert_eq!(
        missing_ref["body"]["detail"],
        "product_ref or product_code is required"
    );

    let unknown = preview(
        &engine,
        "adult",
        json!({"product_ref":"SAFE","long_term":true,"extra":1}),
    );
    assert_eq!(unknown["status"], 400);
    assert_eq!(unknown["body"]["detail"], "unknown fields: extra");

    let person_missing = preview(
        &engine,
        "missing",
        json!({"product_ref":"SAFE","long_term":true}),
    );
    assert_eq!(person_missing["status"], 404);
    assert_eq!(person_missing["body"]["detail"], "person not found");

    let product_missing = preview(
        &engine,
        "adult",
        json!({"product_ref":"MISSING","long_term":true}),
    );
    assert_eq!(product_missing["status"], 404);
    assert_eq!(product_missing["body"]["detail"], "product not found");

    let non_object: Value = serde_json::from_str(&engine.request(
        "POST",
        "/api/people/adult/medications/preview",
        "[]",
    ))
    .expect("non-object preview response json");
    assert_eq!(non_object["status"], 400);
    assert_eq!(
        non_object["body"]["detail"],
        "request body must be a JSON object"
    );

    let via_product_code = preview(
        &engine,
        "adult",
        json!({"product_code":"SAFE","long_term":true}),
    );
    assert_eq!(via_product_code["status"], 200);
    assert_eq!(via_product_code["body"]["product"]["product_ref"], "SAFE");

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn preview_route_keeps_reference_disabled_envelope() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let mut engine = engine(&reference, &personal);
    engine
        .set_reference_available(false, Some("update_required"))
        .expect("disable reference");

    let result = preview(
        &engine,
        "adult",
        json!({"product_ref":"SAFE","long_term":true}),
    );
    assert_eq!(result["status"], 503);
    assert_eq!(
        result["body"]["detail"],
        "reference data unavailable; app update required"
    );
    assert_eq!(result["body"]["reference_status"], "update_required");

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn safe_adult_preview_preserves_public_shape_without_warning_token() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    let engine = engine(&reference, &personal);

    let result = preview(
        &engine,
        "adult",
        json!({
            "product_ref":"SAFE",
            "dose_amount":1,
            "dose_unit":"정",
            "frequency_per_day":2,
            "schedule_times":["08:00","20:00"],
            "administration_route":"oral",
            "prescription_days":7,
            "start_date":"2026-08-20"
        }),
    );
    assert_eq!(result["status"], 200);
    let body = &result["body"];
    assert_eq!(body["person"]["id"], "adult");
    assert_eq!(body["product"]["product_ref"], "SAFE");
    assert_eq!(body["draft"]["end_date"], "2026-08-26");
    assert_eq!(body["current_medication_count"], 0);
    assert_eq!(body["risks"], json!([]));
    assert_eq!(body["review_items"], json!([]));
    assert_eq!(body["dur_checks"].as_array().map(Vec::len), Some(7));
    assert_eq!(body["warning_token"], Value::Null);
    assert_eq!(body["coverage"]["status"], "complete");

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn pediatric_and_duplicate_reviews_remain_fail_closed_and_token_bound() {
    let reference = temp_reference_db();
    let personal = temp_personal_db();
    insert_duplicate(&personal);
    let engine = engine(&reference, &personal);

    let child = preview(
        &engine,
        "child",
        json!({
            "product_ref":"SAFE",
            "dose_amount":1,
            "dose_unit":"정",
            "prescription_days":7,
            "start_date":"2026-08-20"
        }),
    );
    assert_eq!(child["status"], 200);
    assert_eq!(
        child["body"]["quantitative_checks"]["dose"]["result"],
        "not_evaluable"
    );
    assert_eq!(
        child["body"]["quantitative_checks"]["dose"]["pediatric_review"],
        true
    );
    assert_eq!(
        child["body"]["warning_token"],
        "1756c70efca2607d41352ec493ef252505857a56d27df2dc5e3b14263ec75b42"
    );

    let duplicate = preview(
        &engine,
        "adult",
        json!({
            "product_ref":"SAFE",
            "dose_amount":1,
            "dose_unit":"정",
            "frequency_per_day":2,
            "schedule_times":["08:00","20:00"],
            "administration_route":"oral",
            "prescription_days":7,
            "start_date":"2026-08-20"
        }),
    );
    assert_eq!(duplicate["status"], 200);
    assert_eq!(duplicate["body"]["current_medication_count"], 1);
    assert_eq!(
        duplicate["body"]["review_items"].as_array().map(Vec::len),
        Some(1)
    );
    assert_eq!(
        duplicate["body"]["review_items"][0]["type"],
        "duplicate_regimen"
    );
    assert_eq!(
        duplicate["body"]["review_items"][0]["related_medication_id"],
        "existing-safe"
    );
    assert!(duplicate["body"]["warning_token"].as_str().is_some());

    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}
