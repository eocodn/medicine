use medicine_core::inspect_interaction_risks;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_reference_db() -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    let path = std::env::temp_dir().join(format!("medicine-interaction-safety-{nonce}.sqlite"));
    let con = Connection::open(&path).expect("create interaction fixture");
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
            item_seq TEXT,category TEXT,flag_code TEXT,flag_name TEXT,ingredient_name TEXT,
            dosage_form TEXT,details TEXT,change_date TEXT,source_dataset_key TEXT,
            source_row INTEGER,flag_ordinal INTEGER
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
    .expect("create interaction schema");

    for (item, name, ingredient, form) in [
        ("COM-A", "병용A", "ComboA", "정제"),
        ("COM-B", "병용B", "ComboB", "정제"),
        ("SEP-A", "간격A", "SepA", "정제"),
        ("SEP-B", "간격B", "SepB", "정제"),
        ("WASH-A", "중단A", "WashA", "정제"),
        ("WASH-B", "중단B", "WashB", "정제"),
        ("NFKC-A", "정규화대상", "Target", "정제"),
        ("NFKC-B", "정규화원천", "Ｉｔｒａｃｏｎａｚｏｌｅ", "정제"),
        ("UN-A", "미연결A", "UnlinkedA", "정제"),
        ("UN-B", "미연결B", "UnlinkedB", "정제"),
        ("DUP-A", "중복A", "DupA", "정제"),
        ("DUP-B", "중복B", "DupB", "정제"),
    ] {
        con.execute(
            "INSERT INTO products(
                item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                permit_date,cancel_date,cancel_name,permit_status
             ) VALUES(?,?,?,?,?,'2020-01-01',NULL,'정상','active')",
            params![item, name, "제약", ingredient, form],
        )
        .expect("insert product");
    }

    insert_combination(
        &con,
        1,
        101,
        "COM-A",
        "COM-B",
        "ComboA",
        "ComboB",
        "병용금기",
        None,
    );
    insert_combination(
        &con,
        2,
        102,
        "SEP-A",
        "SEP-B",
        "SepA",
        "SepB",
        "병용금기",
        Some("24시간 이내 병용금기"),
    );
    insert_combination(
        &con,
        3,
        103,
        "WASH-A",
        "WASH-B",
        "WashA",
        "WashB",
        "WashA 중단한 직후에는 WashB 시작할 수 없음",
        None,
    );
    insert_combination(
        &con,
        7,
        107,
        "NFKC-A",
        "NFKC-B",
        "Target",
        "Ｉｔｒａｃｏｎａｚｏｌｅ",
        "Itraconazole 투여 중 및 종료 후 2주 간 해당 성분 투여 금기",
        None,
    );
    con.execute(
        "INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
            paired_ingredient_name,paired_ingredient_name_en,dosage_form,details
         ) VALUES(
            4,'fixture:product',4,'combination_contraindication','UN-A','D-UN-A',
            'UnlinkedA','UnlinkedA','UN-B','D-UN-B','UnlinkedB','UnlinkedB','정제','미연결 병용금기'
         )",
        [],
    )
    .expect("insert unlinked combination");

    insert_duplication(&con, 5, "DUP-A", "DupA", "수면제");
    insert_duplication(&con, 6, "DUP-B", "DupB", "수면제");

    con.execute(
        "INSERT INTO reference_semantic_expectations(criterion_rule_id,expected_fact_count)
         VALUES(102,1)",
        [],
    )
    .expect("insert separation expectation");
    con.execute(
        "INSERT INTO reference_criterion_semantics(
            criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
            fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
         ) VALUES(
            102,0,'applicability_condition','runtime_evaluable','minimum_separation',
            'review_required','interaction_timing','24시간 이내 병용금기',
            '{\"direction\":\"symmetric\",\"hours\":24}','24시간 이내 병용금기'
         )",
        [],
    )
    .expect("insert separation semantic");

    drop(con);
    path
}

#[allow(clippy::too_many_arguments)]
fn insert_combination(
    con: &Connection,
    product_rule_id: i64,
    criterion_rule_id: i64,
    left: &str,
    right: &str,
    left_ingredient: &str,
    right_ingredient: &str,
    details: &str,
    qualifier_note: Option<&str>,
) {
    con.execute(
        "INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
            paired_ingredient_name,paired_ingredient_name_en,dosage_form,details
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            product_rule_id,
            "fixture:product",
            product_rule_id,
            "combination_contraindication",
            left,
            format!("D-{left}"),
            left_ingredient,
            left_ingredient,
            right,
            format!("D-{right}"),
            right_ingredient,
            right_ingredient,
            "정제",
            details,
        ],
    )
    .expect("insert combination rule");
    con.execute(
        "INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id) VALUES(?,?)",
        params![product_rule_id, criterion_rule_id],
    )
    .expect("insert combination link");
    con.execute(
        "INSERT INTO product_rule_criteria(
            criterion_rule_id,product_source_dataset_key,product_source_row,
            criterion_source_dataset_key,criterion_source_row,category,item_seq,
            ingredient_code,ingredient_name,ingredient_name_en,paired_item_seq,
            paired_ingredient_code,paired_ingredient_name,paired_ingredient_name_en,
            product_dosage_form,product_details,criterion_ingredient_name,
            criterion_paired_ingredient_name,criterion_qualifier_note,criterion_details,
            match_method,pair_orientation
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        params![
            criterion_rule_id,
            "fixture:product",
            product_rule_id,
            "mfds_dur_ingredient:fixture",
            criterion_rule_id,
            "combination_contraindication",
            left,
            format!("D-{left}"),
            left_ingredient,
            left_ingredient,
            right,
            format!("D-{right}"),
            right_ingredient,
            right_ingredient,
            "정제",
            details,
            left_ingredient,
            right_ingredient,
            qualifier_note,
            details,
            "mfds_ingredient_code",
            "forward",
        ],
    )
    .expect("insert combination runtime row");
}

fn insert_duplication(
    con: &Connection,
    product_rule_id: i64,
    item_seq: &str,
    ingredient: &str,
    effect: &str,
) {
    con.execute(
        "INSERT INTO product_rules(
            id,source_dataset_key,source_row,category,item_seq,ingredient_code,
            ingredient_name,ingredient_name_en,effect_name,dosage_form,details
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        params![
            product_rule_id,
            "fixture:product",
            product_rule_id,
            "therapeutic_duplication_caution",
            item_seq,
            format!("D-{item_seq}"),
            ingredient,
            ingredient,
            effect,
            "정제",
            "효능군 중복",
        ],
    )
    .expect("insert duplication rule");
}

fn inspect(path: &Path, product_ref: &str, current: Value, course: Value) -> Value {
    serde_json::from_str(&inspect_interaction_risks(
        Some(path),
        product_ref,
        &current.to_string(),
        &course.to_string(),
    ))
    .expect("decode interaction risks")
}

fn current_med(id: &str, name: &str, product_ref: &str, start: &str, end: Option<&str>) -> Value {
    json!({
        "id": id,
        "product_name": name,
        "product_ref": product_ref,
        "catalog_item_seq": product_ref,
        "product_mapping_status": "matched",
        "canonical_resolution_issues": {},
        "dosage_form": "정제",
        "start_date": start,
        "end_date": end,
        "active": true
    })
}

#[test]
fn combination_requires_course_overlap_without_timing_extension() {
    let db = temp_reference_db();
    let current = json!([current_med(
        "med-b",
        "병용B",
        "COM-B",
        "2026-08-01",
        Some("2026-08-10")
    )]);
    let overlapping = inspect(
        &db,
        "COM-A",
        current.clone(),
        json!({"start_date":"2026-08-05","end_date":"2026-08-06"}),
    );
    let finding = &overlapping["body"]["risks"][0];
    assert_eq!(finding["type"], "combination_contraindication");
    assert_eq!(finding["severity"], "danger");
    assert_eq!(finding["title"], "병용B와 병용금기");
    assert_eq!(finding["timing"]["kind"], "course_overlap");

    let separated = inspect(
        &db,
        "COM-A",
        current,
        json!({"start_date":"2026-09-01","end_date":"2026-09-02"}),
    );
    assert_eq!(separated["body"]["risks"], json!([]));
    fs::remove_file(db).ok();
}

#[test]
fn materialized_minimum_separation_extends_pair_relevance() {
    let db = temp_reference_db();
    let current = json!([current_med(
        "sep-b",
        "간격B",
        "SEP-B",
        "2026-08-01",
        Some("2026-08-10")
    )]);
    let within = inspect(
        &db,
        "SEP-A",
        current.clone(),
        json!({"start_date":"2026-08-11","end_date":"2026-08-12"}),
    );
    let finding = &within["body"]["risks"][0];
    assert_eq!(finding["timing"]["kind"], "minimum_separation");
    assert_eq!(finding["timing"]["hours"], 24);
    assert_eq!(finding.get("evaluation_status"), None);
    assert_eq!(finding["qualifiers"][0]["requires_review"], false);

    let outside = inspect(
        &db,
        "SEP-A",
        current,
        json!({"start_date":"2026-08-12","end_date":"2026-08-13"}),
    );
    assert_eq!(outside["body"]["risks"], json!([]));
    fs::remove_file(db).ok();
}

#[test]
fn malformed_known_semantic_payload_fails_closed_instead_of_becoming_zero_hours() {
    let db = temp_reference_db();
    let con = Connection::open(&db).expect("open interaction fixture");
    con.execute(
        "UPDATE reference_criterion_semantics
         SET structured_payload_json='{}'
         WHERE criterion_rule_id=102",
        [],
    )
    .expect("corrupt minimum-separation payload");
    drop(con);

    let result = inspect(
        &db,
        "SEP-A",
        json!([current_med(
            "sep-b",
            "간격B",
            "SEP-B",
            "2026-08-01",
            Some("2026-08-10")
        )]),
        json!({"start_date":"2026-08-11","end_date":"2026-08-12"}),
    );
    let finding = &result["body"]["risks"][0];
    assert_eq!(finding["evaluation_status"], "conditional");
    assert_eq!(finding["timing"]["status"], "not_evaluable");
    assert_eq!(finding["timing"]["kind"], "review_required_condition");
    fs::remove_file(db).ok();
}

#[test]
fn unresolved_post_course_timing_stays_conditional_even_when_courses_do_not_overlap() {
    let db = temp_reference_db();
    let result = inspect(
        &db,
        "WASH-B",
        json!([current_med(
            "wash-a",
            "중단A",
            "WASH-A",
            "2026-08-01",
            Some("2026-08-03")
        )]),
        json!({"start_date":"2026-08-10","end_date":"2026-08-12"}),
    );
    let finding = &result["body"]["risks"][0];
    assert_eq!(finding["evaluation_status"], "conditional");
    assert_eq!(finding["timing"]["status"], "not_evaluable");
    assert_eq!(finding["timing"]["kind"], "post_course_restriction");
    assert!(finding["details"]
        .as_str()
        .is_some_and(|value| value.contains("복용 간격 조건")));
    fs::remove_file(db).ok();
}

#[test]
fn washout_source_matching_uses_nfkc_like_python_runtime() {
    let db = temp_reference_db();
    let result = inspect(
        &db,
        "NFKC-A",
        json!([current_med(
            "nfkc-b",
            "정규화원천",
            "NFKC-B",
            "2026-08-01",
            Some("2026-08-07")
        )]),
        json!({"start_date":"2026-08-20","end_date":"2026-08-22"}),
    );
    let finding = &result["body"]["risks"][0];
    assert_eq!(finding["timing"]["status"], "structured");
    assert_eq!(finding["timing"]["kind"], "washout_after");
    assert_eq!(finding["timing"]["source_side"], "right");
    assert_eq!(finding.get("evaluation_status"), None);
    fs::remove_file(db).ok();
}

#[test]
fn unlinked_combination_is_visible_as_unknown() {
    let db = temp_reference_db();
    let result = inspect(
        &db,
        "UN-A",
        json!([current_med("un-b", "미연결B", "UN-B", "2026-08-01", None)]),
        json!({"start_date":"2026-08-22"}),
    );
    let finding = &result["body"]["risks"][0];
    assert_eq!(finding["severity"], "info");
    assert_eq!(finding["evaluation_status"], "unknown");
    assert_eq!(finding["title"], "미연결B와 병용금기 기준 확인 필요");
    fs::remove_file(db).ok();
}

#[test]
fn therapeutic_duplication_requires_overlapping_course_and_shared_group() {
    let db = temp_reference_db();
    let current = json!([current_med(
        "dup-b",
        "중복B",
        "DUP-B",
        "2026-08-01",
        Some("2026-08-31")
    )]);
    let overlapping = inspect(
        &db,
        "DUP-A",
        current.clone(),
        json!({"start_date":"2026-08-22","end_date":"2026-08-25"}),
    );
    let finding = &overlapping["body"]["risks"][0];
    assert_eq!(finding["type"], "therapeutic_duplication_caution");
    assert_eq!(finding["severity"], "warning");
    assert_eq!(finding["title"], "효능군 중복주의 · 수면제");
    assert_eq!(finding["related_medication_id"], "dup-b");

    let separated = inspect(
        &db,
        "DUP-A",
        current,
        json!({"start_date":"2026-09-10","end_date":"2026-09-12"}),
    );
    assert_eq!(separated["body"]["risks"], json!([]));
    fs::remove_file(db).ok();
}
