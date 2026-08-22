mod common;

use medicine_core::inspect_safety_basis;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

fn temp_reference_db() -> PathBuf {
    let path = common::temp_sqlite_path("safety-basis");
    let con = Connection::open(&path).expect("create safety basis fixture");
    con.execute_batch(
        r#"
        CREATE TABLE canonical_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE source_snapshots(
            dataset_key TEXT PRIMARY KEY,
            source_family TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            fetched_at TEXT,
            effective_date TEXT
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
            id INTEGER PRIMARY KEY,
            source_dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            item_seq TEXT NOT NULL,
            ingredient_code TEXT,
            ingredient_name TEXT,
            ingredient_name_en TEXT,
            paired_item_seq TEXT,
            paired_ingredient_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_name_en TEXT,
            effect_name TEXT,
            dosage_form TEXT,
            details TEXT,
            notification_date TEXT,
            change_date TEXT
        );
        CREATE TABLE product_flags(
            source_dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            flag_ordinal INTEGER NOT NULL,
            item_seq TEXT NOT NULL,
            category TEXT NOT NULL,
            flag_code TEXT NOT NULL,
            flag_name TEXT NOT NULL,
            ingredient_name TEXT,
            dosage_form TEXT,
            details TEXT,
            change_date TEXT
        );
        CREATE TABLE ingredient_rules(
            id INTEGER PRIMARY KEY,
            source_dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            sequence_text TEXT,
            ingredient_name TEXT,
            ingredient_name_ko TEXT,
            paired_ingredient_name TEXT,
            rule_value TEXT,
            dosage_form TEXT,
            note TEXT,
            qualifier_note TEXT,
            details TEXT
        );
        CREATE TABLE dose_criteria(
            criterion_rule_id INTEGER PRIMARY KEY,
            maximum_daily_amount TEXT,
            maximum_daily_unit TEXT,
            parse_status TEXT NOT NULL,
            parse_reason TEXT
        );
        CREATE TABLE product_criterion_links(
            product_rule_id INTEGER NOT NULL,
            criterion_rule_id INTEGER NOT NULL,
            match_method TEXT NOT NULL,
            pair_orientation TEXT
        );
        CREATE TABLE reference_semantic_expectations(
            criterion_rule_id INTEGER PRIMARY KEY,
            expected_fact_count INTEGER NOT NULL
        );
        CREATE TABLE reference_criterion_semantics(
            criterion_rule_id INTEGER NOT NULL,
            ordinal INTEGER NOT NULL,
            semantic_role TEXT NOT NULL,
            evaluation_mode TEXT NOT NULL,
            evaluator_kind TEXT NOT NULL,
            fallback_action TEXT NOT NULL,
            qualifier_type TEXT NOT NULL,
            display_text TEXT NOT NULL,
            structured_payload_json TEXT NOT NULL,
            source_remark TEXT NOT NULL,
            PRIMARY KEY(criterion_rule_id,ordinal)
        );
        CREATE VIEW product_rule_criteria AS
        SELECT
            i.id AS criterion_rule_id,
            r.source_dataset_key AS product_source_dataset_key,
            r.source_row AS product_source_row,
            i.source_dataset_key AS criterion_source_dataset_key,
            i.source_row AS criterion_source_row,
            r.category,
            r.item_seq,
            r.ingredient_code,
            r.ingredient_name,
            r.ingredient_name_en,
            r.paired_item_seq,
            r.paired_ingredient_code,
            r.paired_ingredient_name,
            r.paired_ingredient_name_en,
            r.effect_name,
            r.dosage_form AS product_dosage_form,
            r.details AS product_details,
            i.sequence_text AS criterion_sequence_text,
            i.ingredient_name AS criterion_ingredient_name,
            i.ingredient_name_ko AS criterion_ingredient_name_ko,
            i.paired_ingredient_name AS criterion_paired_ingredient_name,
            i.rule_value AS criterion_rule_value,
            i.dosage_form AS criterion_dosage_form,
            i.note AS criterion_note,
            i.qualifier_note AS criterion_qualifier_note,
            i.details AS criterion_details,
            d.maximum_daily_amount AS criterion_maximum_daily_amount,
            d.maximum_daily_unit AS criterion_maximum_daily_unit,
            d.parse_status AS criterion_dose_parse_status,
            d.parse_reason AS criterion_dose_parse_reason,
            l.match_method,
            l.pair_orientation
        FROM product_criterion_links l
        JOIN product_rules r ON r.id=l.product_rule_id
        JOIN ingredient_rules i ON i.id=l.criterion_rule_id
        LEFT JOIN dose_criteria d ON d.criterion_rule_id=i.id;

        INSERT INTO canonical_meta(key,value) VALUES
            ('schema_version','10'),
            ('build_stage','complete'),
            ('built_at','2026-08-13T15:00:00+09:00');
        INSERT INTO source_snapshots(dataset_key,source_family,sha256,row_count,fetched_at,effective_date)
        VALUES('fixture:one','mfds_permit_api','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1,NULL,NULL);

        INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_text,dosage_form,
            permit_date,cancel_date,cancel_name,permit_status
        ) VALUES
            ('P-Z','정량제품','제약','Drug Z','정제','2020-01-01',NULL,'정상','active'),
            ('P-U','미해결제품','제약','Drug U','정제','2020-01-01',NULL,'정상','active');

        INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,dosage_form,details
        ) VALUES
            (1,'fixture:one',1,'duration_caution','P-Z','D-Z','Drug Z','Drug Z','정제','최대 28일'),
            (2,'fixture:one',2,'dose_caution','P-Z','D-Z','Drug Z','Drug Z','정제','1일 최대 100mg'),
            (3,'fixture:one',3,'pregnancy_contraindication','P-U','D-U','Drug U','Drug U','정제','임부금기'),
            (4,'fixture:one',4,'duration_caution','P-U','D-U','Drug U','Drug U','정제','최대 7일');

        INSERT INTO ingredient_rules(
            id,source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
            rule_value,dosage_form,details
        ) VALUES
            (11,'fixture:criterion',11,'duration_caution','Drug Z','Drug Z','28일','정제','최대 28일'),
            (12,'fixture:criterion',12,'dose_caution','Drug Z','Drug Z','100밀리그램','정제','1일 최대 100mg');
        INSERT INTO dose_criteria(
            criterion_rule_id,maximum_daily_amount,maximum_daily_unit,parse_status,parse_reason
        ) VALUES(12,'100','mg','parsed',NULL);
        INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id,match_method,pair_orientation)
        VALUES
            (1,11,'mfds_ingredient_code',NULL),
            (2,12,'mfds_ingredient_code',NULL);
        "#,
    )
    .expect("create safety basis schema");
    drop(con);
    path
}

fn inspect(path: &Path, product_ref: &str, person: Value, draft: Value) -> Value {
    serde_json::from_str(&inspect_safety_basis(
        Some(path),
        product_ref,
        &person.to_string(),
        &draft.to_string(),
    ))
    .expect("decode safety basis")
}

#[test]
fn safety_basis_reports_verified_manifest_and_quantitative_boundaries() {
    let reference = temp_reference_db();
    let result = inspect(
        &reference,
        "P-Z",
        json!({
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant"
        }),
        json!({
            "dose_amount": "20",
            "dose_unit": "mg",
            "frequency_per_day": 4,
            "prescription_days": 35,
            "start_date": "2026-08-20"
        }),
    );
    assert_eq!(result["status"], 200);
    let body = &result["body"];
    assert_eq!(body["dataset"]["status"], "verified");
    assert_eq!(body["dataset"]["schema_version"], "10");
    assert_eq!(body["dataset"]["source_count"], 1);
    assert_eq!(
        body["dataset"]["source_families"],
        json!(["mfds_permit_api"])
    );
    assert!(body["dataset"]["dataset_id"]
        .as_str()
        .is_some_and(|value| value.starts_with("sha256:")));
    assert_eq!(body["coverage"]["status"], "complete");
    assert_eq!(body["coverage"]["product"]["item_seq"], "P-Z");
    assert_eq!(body["coverage"]["ingredient"]["status"], "not_required");

    let duration = &body["quantitative_checks"]["duration"];
    assert_eq!(duration["result"], "exceeded");
    assert_eq!(duration["requested_days"], 35);
    assert_eq!(duration["maximum_days"], 28);
    assert_eq!(duration["source_rows"].as_array().map(Vec::len), Some(1));

    let dose = &body["quantitative_checks"]["dose"];
    assert_eq!(dose["result"], "within");
    assert_eq!(dose["daily_amount"], 80.0);
    assert_eq!(dose["maximum_daily_amount"], 100.0);
    assert_eq!(dose["unit"], "mg");
    assert_eq!(dose["source_rows"].as_array().map(Vec::len), Some(1));

    fs::remove_file(reference).ok();
}

#[test]
fn safety_basis_fails_closed_for_unlinked_rules_and_relevant_profile_gaps() {
    let reference = temp_reference_db();
    let result = inspect(
        &reference,
        "P-U",
        json!({
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "unknown"
        }),
        json!({"prescription_days": 10, "start_date": "2026-08-20"}),
    );
    assert_eq!(result["status"], 200);
    let body = &result["body"];
    assert_eq!(body["coverage"]["status"], "limited");
    assert_eq!(
        body["coverage"]["category_resolution"]["pregnancy_contraindication"],
        "unresolved"
    );
    assert_eq!(
        body["coverage"]["category_resolution"]["duration_caution"],
        "unresolved"
    );
    let categories = body["coverage"]["not_evaluable_checks"]
        .as_array()
        .expect("coverage checks")
        .iter()
        .filter_map(|item| item["category"].as_str())
        .collect::<Vec<_>>();
    assert!(categories.contains(&"pregnancy_contraindication"));
    assert!(categories.contains(&"duration_caution"));
    assert_eq!(
        body["quantitative_checks"]["duration"]["result"],
        "not_evaluable"
    );
    assert_eq!(
        body["quantitative_checks"]["duration"]["reason"],
        "canonical duration product rule is not linked to one criterion"
    );

    fs::remove_file(reference).ok();
}

#[test]
fn count_dose_against_mass_threshold_remains_not_evaluable() {
    let reference = temp_reference_db();
    let result = inspect(
        &reference,
        "P-Z",
        json!({
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable"
        }),
        json!({
            "dose_amount": 1,
            "dose_unit": "정",
            "frequency_per_day": 2,
            "long_term": true
        }),
    );
    assert_eq!(result["status"], 200);
    assert_eq!(
        result["body"]["quantitative_checks"]["dose"]["result"],
        "not_evaluable"
    );
    assert_eq!(
        result["body"]["quantitative_checks"]["dose"]["reason"],
        "count dose requires an authoritative per-unit ingredient content"
    );

    fs::remove_file(reference).ok();
}

#[test]
fn materialized_reference_semantics_keep_qualified_dose_conditional() {
    let reference = temp_reference_db();
    let con = Connection::open(&reference).expect("open safety basis fixture");
    con.execute(
        "INSERT INTO reference_semantic_expectations(criterion_rule_id,expected_fact_count) VALUES(12,1)",
        [],
    )
    .expect("insert semantic expectation");
    con.execute(
        "INSERT INTO reference_criterion_semantics(
             criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
             fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
         ) VALUES(12,0,'applicability_condition','review_required','opaque_condition',
                  'review_required','indication',?1,'{}',?2)",
        [
            "적응증에 따라 Drug Z 1일 최대용량이 다름",
            "적응증에 따른 최대용량 확인 필요",
        ],
    )
    .expect("insert semantic fact");
    drop(con);

    let result = inspect(
        &reference,
        "P-Z",
        json!({
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable"
        }),
        json!({
            "dose_amount": "20",
            "dose_unit": "mg",
            "frequency_per_day": 4,
            "long_term": true
        }),
    );
    assert_eq!(result["status"], 200);
    let dose = &result["body"]["quantitative_checks"]["dose"];
    assert_eq!(dose["result"], "not_evaluable");
    assert_eq!(dose["evaluation_status"], "conditional");
    assert_eq!(
        dose["reason"],
        "MFDS dose criterion has a qualifier requiring professional review"
    );
    assert_eq!(dose["qualifiers"][0]["type"], "indication");
    assert_eq!(
        dose["qualifiers"][0]["text"],
        "적응증에 따라 Drug Z 1일 최대용량이 다름"
    );
    assert_eq!(dose["qualifiers"][0]["requires_review"], true);

    fs::remove_file(reference).ok();
}
