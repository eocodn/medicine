mod common;

use medicine_core::{inspect_product, normalize_prescription_draft};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn temp_canonical_db() -> PathBuf {
    let path = common::temp_sqlite_path("safety-substrate");
    let con = Connection::open(&path).expect("create canonical fixture");
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
         CREATE TABLE product_identifiers(
             item_seq TEXT NOT NULL,
             system TEXT NOT NULL,
             value TEXT NOT NULL
         );
         CREATE TABLE product_rules(
             id INTEGER PRIMARY KEY,
             category TEXT NOT NULL,
             item_seq TEXT NOT NULL,
             paired_item_seq TEXT,
             effect_name TEXT,
             dosage_form TEXT,
             source_dataset_key TEXT NOT NULL,
             source_row INTEGER NOT NULL
         );
         CREATE TABLE product_flags(
             item_seq TEXT NOT NULL,
             category TEXT NOT NULL,
             flag_code TEXT NOT NULL,
             flag_name TEXT NOT NULL,
             ingredient_name TEXT,
             dosage_form TEXT,
             details TEXT,
             change_date TEXT,
             source_dataset_key TEXT NOT NULL,
             source_row INTEGER NOT NULL,
             flag_ordinal INTEGER NOT NULL
         );
         CREATE TABLE product_criterion_links(
             product_rule_id INTEGER NOT NULL,
             criterion_rule_id INTEGER NOT NULL
         );
         CREATE TABLE product_rule_criteria(item_seq TEXT NOT NULL, category TEXT NOT NULL);

         INSERT INTO products(
             item_seq,product_name,manufacturer,ingredient_text,dosage_form,
             permit_date,cancel_date,cancel_name,permit_status
         ) VALUES(
             'P-Z','졸피뎀정','제약','Zolpidem','정제','2020-01-01',NULL,NULL,'active'
         );
         INSERT INTO product_identifiers(item_seq,system,value) VALUES
             ('P-Z','EDI','E2'),('P-Z','EDI','E1'),('P-Z','OTHER','ignore');
         INSERT INTO product_rules(
             id,category,item_seq,paired_item_seq,effect_name,dosage_form,source_dataset_key,source_row
         ) VALUES
             (1,'duration_caution','P-Z',NULL,NULL,'필름코팅정','src',1),
             (2,'elderly_caution','P-Z',NULL,NULL,NULL,'src',2),
             (3,'age_contraindication','P-Z',NULL,NULL,NULL,'src',3),
             (4,'therapeutic_duplication_caution','P-Z',NULL,'수면제',NULL,'src',4);
         INSERT INTO product_criterion_links(product_rule_id,criterion_rule_id) VALUES(1,11);
         INSERT INTO product_rule_criteria(item_seq,category) VALUES('P-Z','duration_caution');
         INSERT INTO product_flags(
             item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,details,
             change_date,source_dataset_key,source_row,flag_ordinal
         ) VALUES(
             'P-Z','split_caution','S','분할주의','Zolpidem',NULL,'분할불가',
             '2026-01-01','flags',9,0
         );",
    )
    .expect("create canonical product fixture");
    drop(con);
    path
}

fn decode(raw: &str) -> Value {
    serde_json::from_str(raw).expect("decode substrate response")
}

#[test]
fn canonical_product_resolution_preserves_runtime_identity_and_coverage_metadata() {
    let canonical = temp_canonical_db();
    let product = decode(&inspect_product(Some(canonical.as_path()), " P-Z "));
    assert_eq!(product["status"], 200);
    let body = &product["body"];
    assert_eq!(body["product_ref"], "P-Z");
    assert_eq!(body["catalog_item_seq"], "P-Z");
    assert_eq!(body["product_code"], "P-Z");
    assert_eq!(body["edi_codes"], json!(["E1", "E2"]));
    assert_eq!(body["matched_product_codes"], json!(["P-Z"]));
    assert_eq!(body["product_mapping_status"], "matched");
    assert_eq!(body["product_mapping_method"], "item_seq_exact");
    assert_eq!(body["product_identity_status"], "matched");
    assert_eq!(body["product_identity_method"], "item_seq_exact");
    assert_eq!(body["product_name"], "졸피뎀정");
    assert_eq!(body["ingredient_name"], "Zolpidem");
    assert_eq!(body["manufacturer"], "제약");
    assert_eq!(
        body["canonical_dosage_forms"],
        json!(["정제", "필름코팅정"])
    );
    assert_eq!(body["suggested_administration_route"], "oral");
    assert_eq!(body["permit_status"], "active");
    assert_eq!(body["catalog_source"], "canonical");
    assert_eq!(body["dur_match"], true);
    assert_eq!(body["dur_coverage_status"], "partial");
    assert_eq!(
        body["canonical_linked_categories"],
        json!([
            "duration_caution",
            "elderly_caution",
            "therapeutic_duplication_caution"
        ])
    );
    assert_eq!(
        body["canonical_resolution_issues"]["age_contraindication"],
        1
    );
    assert_eq!(body["product_flags"][0]["category"], "split_caution");
    assert_eq!(body["product_flags"][0]["details"], "분할불가");

    let missing = decode(&inspect_product(Some(canonical.as_path()), "missing"));
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "product not found");
    let blank = decode(&inspect_product(Some(canonical.as_path()), "  "));
    assert_eq!(blank["status"], 400);
    assert_eq!(blank["body"]["detail"], "product_ref is required");

    fs::remove_file(canonical).ok();
}

#[test]
fn prescription_normalization_and_hash_match_python_contract() {
    let normalized = decode(&normalize_prescription_draft(
        &json!({
            "dose_amount": "1.00",
            "dose_unit": "정",
            "schedule_times": ["8:00", "20:00"],
            "prescription_days": "5",
            "start_date": "2026-08-20"
        })
        .to_string(),
        Some("person-1"),
        Some("P-Z"),
    ));
    assert_eq!(normalized["status"], 200);
    assert_eq!(
        normalized["body"]["draft"],
        json!({
            "dosage_text": "1정",
            "dose_amount": "1",
            "dose_unit": "정",
            "frequency_per_day": 2,
            "meal_relation": "unspecified",
            "administration_route": "unknown",
            "as_needed": false,
            "prn_max_per_day": null,
            "prescription_days": 5,
            "long_term": false,
            "schedule_times": ["08:00", "20:00"],
            "start_date": "2026-08-20",
            "end_date": "2026-08-24"
        })
    );
    assert_eq!(
        normalized["body"]["draft_hash"],
        "538f7291c8f1d65e3e88b9e4b6db3a32572859b8c18616b2ffb0aa0a3e612de9"
    );

    let duplicate = decode(&normalize_prescription_draft(
        r#"{"schedule_times":["08:00","8:00"],"long_term":true}"#,
        None,
        None,
    ));
    assert_eq!(duplicate["status"], 400);
    assert_eq!(
        duplicate["body"]["detail"],
        "schedule_times must not contain duplicates"
    );

    let prn_fixed = decode(&normalize_prescription_draft(
        r#"{"as_needed":true,"frequency_per_day":1,"long_term":true}"#,
        None,
        None,
    ));
    assert_eq!(prn_fixed["status"], 400);
    assert_eq!(
        prn_fixed["body"]["detail"],
        "PRN/as_needed medication cannot have a fixed daily frequency or schedule"
    );

    let fixed_prn_max = decode(&normalize_prescription_draft(
        r#"{"prn_max_per_day":2,"long_term":true}"#,
        None,
        None,
    ));
    assert_eq!(fixed_prn_max["status"], 400);
    assert_eq!(
        fixed_prn_max["body"]["detail"],
        "prn_max_per_day is only valid for PRN/as_needed medication"
    );

    let conflicting_bound = decode(&normalize_prescription_draft(
        r#"{"long_term":true,"prescription_days":3,"start_date":"2026-08-20"}"#,
        None,
        None,
    ));
    assert_eq!(conflicting_bound["status"], 400);
    assert_eq!(
        conflicting_bound["body"]["detail"],
        "long_term medication cannot also have a prescription duration or end_date"
    );
}
