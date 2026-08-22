mod common;

use medicine_core::inspect_profile_risks;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

fn temp_reference_db() -> PathBuf {
    let path = common::temp_sqlite_path("profile-safety");
    let con = Connection::open(&path).expect("create profile safety fixture");
    con.execute_batch(
        r#"
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
            item_seq TEXT,
            category TEXT,
            flag_code TEXT,
            flag_name TEXT,
            ingredient_name TEXT,
            dosage_form TEXT,
            details TEXT,
            change_date TEXT,
            source_dataset_key TEXT,
            source_row INTEGER,
            flag_ordinal INTEGER
        );
        CREATE TABLE product_criterion_links(product_rule_id INTEGER,criterion_rule_id INTEGER);
        CREATE TABLE product_rule_criteria(
            criterion_rule_id INTEGER,
            product_source_dataset_key TEXT,
            product_source_row INTEGER,
            criterion_source_dataset_key TEXT,
            criterion_source_row INTEGER,
            category TEXT,
            item_seq TEXT,
            ingredient_code TEXT,
            ingredient_name TEXT,
            ingredient_name_en TEXT,
            paired_item_seq TEXT,
            paired_ingredient_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_name_en TEXT,
            effect_name TEXT,
            product_dosage_form TEXT,
            product_details TEXT,
            criterion_sequence_text TEXT,
            criterion_ingredient_name TEXT,
            criterion_ingredient_name_ko TEXT,
            criterion_paired_ingredient_name TEXT,
            criterion_rule_value TEXT,
            criterion_dosage_form TEXT,
            criterion_note TEXT,
            criterion_qualifier_note TEXT,
            criterion_details TEXT,
            criterion_maximum_daily_amount TEXT,
            criterion_maximum_daily_unit TEXT,
            criterion_dose_parse_status TEXT,
            criterion_dose_parse_reason TEXT,
            match_method TEXT,
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
        "#,
    )
    .expect("create profile safety schema");

    for (item, name, form) in [
        ("AGE-T", "세티리진정", None),
        ("AGE-U", "제형미상세티리진", None),
        ("P1", "임부1등급", Some("정제")),
        ("P2", "임부2등급", Some("정제")),
        ("PC", "조건부임부", Some("정제")),
        ("PN", "비고조건임부", Some("정제")),
        ("PA", "등급충돌임부", Some("정제")),
        ("ELD", "노인주의제품", Some("정제")),
    ] {
        con.execute(
            "INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                permit_date,cancel_date,cancel_name,permit_status
             ) VALUES(?,?,?,?,?,'2020-01-01',NULL,'정상','active')",
            params![item, name, "제약", format!("Ingredient-{item}"), form],
        )
        .expect("insert product");
    }

    insert_linked_rule(
        &con,
        1,
        101,
        "AGE-T",
        "age_contraindication",
        "액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        Some("필름코팅정"),
        Some("액제, 정제, 캡슐제"),
        None,
        "연령금기",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        2,
        102,
        "AGE-U",
        "age_contraindication",
        "액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        None,
        Some("액제, 정제, 캡슐제"),
        None,
        "연령금기",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        3,
        103,
        "P1",
        "pregnancy_contraindication",
        "1등급",
        Some("정제"),
        None,
        None,
        "임부금기 1등급",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        4,
        104,
        "P2",
        "pregnancy_contraindication",
        "2",
        Some("정제"),
        None,
        None,
        "임부금기 2등급",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        5,
        105,
        "PC",
        "pregnancy_contraindication",
        "2등급(말라리아 치료시 제외)",
        Some("정제"),
        None,
        None,
        "말라리아 치료 목적이면 예외",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        6,
        106,
        "PN",
        "pregnancy_contraindication",
        "2등급",
        Some("정제"),
        None,
        Some("단, 강심제로 사용시 제외"),
        "강심제 사용 여부에 따라 예외",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        7,
        107,
        "PA",
        "pregnancy_contraindication",
        "1등급",
        Some("정제"),
        None,
        None,
        "적응증 A",
        "mfds_ingredient_code",
    );
    insert_linked_rule(
        &con,
        8,
        108,
        "PA",
        "pregnancy_contraindication",
        "2등급",
        Some("정제"),
        None,
        None,
        "적응증 B",
        "mfds_ingredient_code",
    );
    con.execute(
        "INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,dosage_form,details
         ) VALUES(9,'fixture:product',9,'elderly_caution','ELD','D-ELD',
                  'Ingredient-ELD','Ingredient-ELD','정제','노인에서 주의')",
        [],
    )
    .expect("insert elderly direct rule");

    con.execute(
        "INSERT INTO reference_semantic_expectations(criterion_rule_id,expected_fact_count)
         VALUES(106,1)",
        [],
    )
    .expect("insert semantic expectation");
    con.execute(
        "INSERT INTO reference_criterion_semantics(
            criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
            fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
         ) VALUES(
            106,0,'applicability_condition','review_required','opaque_condition',
            'review_required','indication','강심제로 사용 시 제외','{}','단, 강심제로 사용시 제외'
         )",
        [],
    )
    .expect("insert semantic fact");

    drop(con);
    path
}

#[allow(clippy::too_many_arguments)]
fn insert_linked_rule(
    con: &Connection,
    product_rule_id: i64,
    criterion_rule_id: i64,
    item_seq: &str,
    category: &str,
    rule_value: &str,
    product_form: Option<&str>,
    criterion_form: Option<&str>,
    qualifier_note: Option<&str>,
    details: &str,
    match_method: &str,
) {
    con.execute(
        "INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,dosage_form,details
         ) VALUES(?,?,?,?,?,?,?,?,?,?)",
        params![
            product_rule_id,
            "fixture:product",
            product_rule_id,
            category,
            item_seq,
            format!("D-{item_seq}"),
            format!("Ingredient-{item_seq}"),
            format!("Ingredient-{item_seq}"),
            product_form,
            details,
        ],
    )
    .expect("insert product rule");
    con.execute(
        "INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id) VALUES(?,?)",
        params![product_rule_id, criterion_rule_id],
    )
    .expect("insert rule link");
    con.execute(
        "INSERT INTO product_rule_criteria(
            criterion_rule_id,product_source_dataset_key,product_source_row,
            criterion_source_dataset_key,criterion_source_row,category,item_seq,
            ingredient_code,ingredient_name,ingredient_name_en,product_dosage_form,
            product_details,criterion_sequence_text,criterion_ingredient_name,
            criterion_ingredient_name_ko,criterion_rule_value,criterion_dosage_form,
            criterion_qualifier_note,criterion_details,match_method
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            criterion_rule_id,
            "fixture:product",
            product_rule_id,
            "mfds_dur_ingredient:fixture",
            criterion_rule_id,
            category,
            item_seq,
            format!("D-{item_seq}"),
            format!("Ingredient-{item_seq}"),
            format!("Ingredient-{item_seq}"),
            product_form,
            details,
            criterion_rule_id.to_string(),
            format!("Ingredient-{item_seq}"),
            format!("Ingredient-{item_seq}"),
            rule_value,
            criterion_form,
            qualifier_note,
            details,
            match_method,
        ],
    )
    .expect("insert runtime criterion row");
}

fn inspect(path: &Path, product_ref: &str, person: Value, course: Value, as_of: &str) -> Value {
    serde_json::from_str(&inspect_profile_risks(
        Some(path),
        product_ref,
        &person.to_string(),
        &course.to_string(),
        Some(as_of),
    ))
    .expect("decode profile risks")
}

#[test]
fn age_rules_use_product_form_and_fail_closed_when_form_is_unknown() {
    let db = temp_reference_db();
    let person = json!({
        "birth_date":"2022-01-01",
        "sex":"female",
        "pregnancy_status":"not_pregnant"
    });
    let course = json!({"start_date":"2026-08-13"});

    let matched = inspect(&db, "AGE-T", person.clone(), course.clone(), "2026-08-13");
    assert_eq!(matched["status"], 200);
    let age = matched["body"]["risks"].as_array().expect("risks");
    assert_eq!(age.len(), 1);
    assert_eq!(age[0]["type"], "age_contraindication");
    assert_eq!(age[0]["severity"], "danger");
    assert_eq!(
        age[0]["title"],
        "연령금기 · 액제: 2세 미만, 정제, 캡슐제: 6세 미만"
    );

    let unknown = inspect(&db, "AGE-U", person, course, "2026-08-13");
    let finding = &unknown["body"]["risks"][0];
    assert_eq!(finding["severity"], "info");
    assert_eq!(finding["evaluation_status"], "unknown");
    assert!(finding["details"]
        .as_str()
        .is_some_and(|value| value.contains("제품 제형을 확정하지 못해")));

    fs::remove_file(db).ok();
}

#[test]
fn elderly_risk_checks_both_start_and_end_of_candidate_course() {
    let db = temp_reference_db();
    let result = inspect(
        &db,
        "ELD",
        json!({
            "birth_date":"1961-08-23",
            "sex":"female",
            "pregnancy_status":"not_pregnant"
        }),
        json!({"start_date":"2026-08-22","end_date":"2026-08-24"}),
        "2026-08-22",
    );
    let risks = result["body"]["risks"].as_array().expect("risks");
    assert_eq!(risks.len(), 1);
    assert_eq!(risks[0]["type"], "elderly_caution");
    assert_eq!(risks[0]["severity"], "warning");
    assert_eq!(risks[0]["title"], "노인주의 대상");
    assert_eq!(risks[0]["details"], "노인에서 주의");

    fs::remove_file(db).ok();
}

#[test]
fn pregnancy_grades_preserve_definitive_conditional_and_conflict_semantics() {
    let db = temp_reference_db();
    let pregnant = json!({
        "birth_date":"1990-01-01",
        "sex":"female",
        "pregnancy_status":"pregnant"
    });
    let course = json!({"start_date":"2026-08-22"});

    let grade_one = inspect(&db, "P1", pregnant.clone(), course.clone(), "2026-08-22");
    assert_eq!(grade_one["body"]["risks"][0]["severity"], "danger");
    assert_eq!(grade_one["body"]["risks"][0]["title"], "임부금기 · 1등급");

    let grade_two = inspect(&db, "P2", pregnant.clone(), course.clone(), "2026-08-22");
    assert_eq!(grade_two["body"]["risks"][0]["title"], "임부금기 · 2등급");

    let conditional = inspect(&db, "PC", pregnant.clone(), course.clone(), "2026-08-22");
    assert_eq!(conditional["body"]["risks"][0]["severity"], "danger");
    assert_eq!(
        conditional["body"]["risks"][0]["evaluation_status"],
        "conditional"
    );

    let conflict = inspect(&db, "PA", pregnant.clone(), course.clone(), "2026-08-22");
    let risks = conflict["body"]["risks"].as_array().expect("risks");
    assert_eq!(risks.len(), 1);
    assert_eq!(risks[0]["severity"], "info");
    assert_eq!(risks[0]["title"], "임부금기 기준 확인 필요");
    assert_eq!(risks[0]["evaluation_status"], "unknown");

    let not_pregnant = inspect(
        &db,
        "P1",
        json!({
            "birth_date":"1990-01-01",
            "sex":"female",
            "pregnancy_status":"not_pregnant"
        }),
        course,
        "2026-08-22",
    );
    assert_eq!(not_pregnant["body"]["risks"], json!([]));

    fs::remove_file(db).ok();
}

#[test]
fn materialized_review_required_semantics_keep_pregnancy_finding_conditional() {
    let db = temp_reference_db();
    let result = inspect(
        &db,
        "PN",
        json!({
            "birth_date":"1990-01-01",
            "sex":"female",
            "pregnancy_status":"pregnant"
        }),
        json!({"start_date":"2026-08-22"}),
        "2026-08-22",
    );
    let finding = &result["body"]["risks"][0];
    assert_eq!(finding["title"], "임부금기 · 2등급");
    assert_eq!(finding["evaluation_status"], "conditional");
    assert!(finding["details"]
        .as_str()
        .is_some_and(|value| value.contains("의사 또는 약사")));
    assert_eq!(finding["qualifiers"][0]["text"], "강심제로 사용 시 제외");
    assert_eq!(finding["qualifiers"][0]["requires_review"], true);

    fs::remove_file(db).ok();
}
