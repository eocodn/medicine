mod common;

use medicine_core::{verify_reference_database, ReferenceVerificationError};
use rusqlite::Connection;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

const CONTRACT_MAJOR: u64 = 1;
const DATASET_ID: &str = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn create_reference_db(path: &Path) {
    let con = Connection::open(path).expect("create reference verifier fixture");
    con.execute_batch(
        r#"
        PRAGMA foreign_keys=ON;
        CREATE TABLE reference_contract_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE canonical_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE source_snapshots(
            dataset_key TEXT PRIMARY KEY, source_family TEXT NOT NULL,
            effective_date TEXT, fetched_at TEXT, row_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        );
        CREATE TABLE products(
            item_seq TEXT PRIMARY KEY, product_name TEXT NOT NULL,
            manufacturer TEXT, ingredient_text TEXT, dosage_form TEXT,
            permit_date TEXT, cancel_date TEXT, cancel_name TEXT,
            permit_status TEXT NOT NULL
        );
        CREATE TABLE product_identifiers(
            item_seq TEXT NOT NULL, system TEXT NOT NULL, value TEXT NOT NULL,
            FOREIGN KEY(item_seq) REFERENCES products(item_seq)
        );
        CREATE TABLE product_flags(
            item_seq TEXT NOT NULL, category TEXT NOT NULL, flag_code TEXT NOT NULL,
            flag_name TEXT NOT NULL, ingredient_name TEXT, dosage_form TEXT,
            details TEXT, change_date TEXT, source_dataset_key TEXT,
            source_row INTEGER, flag_ordinal INTEGER
        );
        CREATE TABLE product_rules(
            id INTEGER PRIMARY KEY, source_dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL, category TEXT NOT NULL,
            item_seq TEXT NOT NULL, paired_item_seq TEXT,
            effect_name TEXT, dosage_form TEXT, details TEXT
        );
        CREATE TABLE product_criterion_links(
            product_rule_id INTEGER NOT NULL, criterion_rule_id INTEGER NOT NULL
        );
        CREATE TABLE reference_semantic_expectations(
            criterion_rule_id INTEGER PRIMARY KEY, expected_fact_count INTEGER NOT NULL
        );
        CREATE TABLE reference_criterion_semantics(
            criterion_rule_id INTEGER NOT NULL, ordinal INTEGER NOT NULL,
            semantic_role TEXT NOT NULL, evaluation_mode TEXT NOT NULL,
            evaluator_kind TEXT NOT NULL, fallback_action TEXT NOT NULL,
            qualifier_type TEXT, display_text TEXT,
            structured_payload_json TEXT NOT NULL, source_remark TEXT,
            PRIMARY KEY(criterion_rule_id, ordinal)
        );
        CREATE VIEW product_rule_criteria AS
        SELECT
            r.id AS criterion_rule_id,
            r.source_dataset_key AS product_source_dataset_key,
            r.source_row AS product_source_row,
            r.source_dataset_key AS criterion_source_dataset_key,
            r.source_row AS criterion_source_row,
            r.category, r.item_seq, NULL AS ingredient_name,
            r.paired_item_seq, NULL AS paired_ingredient_name,
            r.effect_name, r.dosage_form AS product_dosage_form,
            r.details AS product_details, NULL AS criterion_ingredient_name,
            NULL AS criterion_paired_ingredient_name, NULL AS criterion_rule_value,
            NULL AS criterion_dosage_form, NULL AS criterion_note,
            NULL AS criterion_qualifier_note, NULL AS criterion_details,
            NULL AS criterion_maximum_daily_amount,
            NULL AS criterion_maximum_daily_unit,
            NULL AS criterion_dose_parse_status, NULL AS criterion_dose_parse_reason,
            'fixture' AS match_method
        FROM product_rules r;
        INSERT INTO reference_contract_meta(key,value) VALUES
            ('contract_major','1'), ('dataset_id', 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
        INSERT INTO canonical_meta(key,value) VALUES ('schema_version','10');
        INSERT INTO source_snapshots(dataset_key,source_family,effective_date,fetched_at,row_count,sha256)
            VALUES ('fixture:one','fixture',NULL,NULL,1,
                    'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
        INSERT INTO products(item_seq,product_name,permit_status) VALUES ('P-1','Fixture product','active');
        INSERT INTO product_rules(id,source_dataset_key,source_row,category,item_seq)
            VALUES (7,'fixture:one',1,'duration_caution','P-1');
        INSERT INTO reference_semantic_expectations(criterion_rule_id,expected_fact_count)
            VALUES (7,1);
        INSERT INTO reference_criterion_semantics(
            criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
            fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
        ) VALUES (7,0,'informational','resolved_at_build','display_only','none',
                  'duration','fixture display','{}','fixture remark');
        "#,
    )
    .expect("create reference verifier schema");
}

fn reference_db(label: &str) -> PathBuf {
    let path = common::temp_sqlite_path(label);
    create_reference_db(&path);
    path
}

fn verify(
    path: &Path,
) -> Result<medicine_core::ReferenceVerificationReport, ReferenceVerificationError> {
    verify_reference_database(path, CONTRACT_MAJOR, DATASET_ID)
}

#[test]
fn verifier_returns_structured_verified_report_for_contract_v1_database() {
    let path = reference_db("reference-verifier-valid");
    let report = verify(&path).expect("valid reference database");
    assert_eq!(report.status, "verified");
    assert_eq!(report.contract_major, CONTRACT_MAJOR);
    assert_eq!(report.dataset_id, DATASET_ID);
    assert!(report.size_bytes > 0);
    fs::remove_file(path).ok();
}

#[test]
fn verifier_opens_reference_database_read_only_and_does_not_mutate_it() {
    let path = reference_db("reference-verifier-read-only");
    let before = fs::read(&path).expect("read fixture before verification");
    let mut permissions = fs::metadata(&path).expect("stat fixture").permissions();
    permissions.set_mode(0o444);
    fs::set_permissions(&path, permissions).expect("make fixture read-only");

    verify(&path).expect("read-only reference database remains verifiable");
    assert_eq!(
        fs::read(&path).expect("read fixture after verification"),
        before
    );

    let mut permissions = fs::metadata(&path).expect("stat fixture").permissions();
    permissions.set_mode(0o644);
    fs::set_permissions(&path, permissions).expect("restore fixture permissions");
    fs::remove_file(path).ok();
}

#[test]
fn verifier_rejects_missing_required_objects_and_columns() {
    let missing_table = reference_db("reference-verifier-missing-table");
    let con = Connection::open(&missing_table).expect("open fixture");
    con.execute("DROP TABLE reference_semantic_expectations", [])
        .expect("remove required object");
    drop(con);
    let error = verify(&missing_table).expect_err("missing required table must fail closed");
    assert!(error
        .to_string()
        .contains("reference_semantic_expectations"));
    fs::remove_file(missing_table).ok();

    let missing_column = reference_db("reference-verifier-missing-column");
    let con = Connection::open(&missing_column).expect("open fixture");
    con.execute("ALTER TABLE products DROP COLUMN manufacturer", [])
        .expect("remove required column");
    drop(con);
    let error = verify(&missing_column).expect_err("missing required column must fail closed");
    assert!(error.to_string().contains("manufacturer"));
    fs::remove_file(missing_column).ok();
}

#[test]
fn verifier_accepts_additional_provenance_columns_but_rejects_bad_metadata() {
    let path = reference_db("reference-verifier-provenance");
    let con = Connection::open(&path).expect("open fixture");
    con.execute(
        "ALTER TABLE source_snapshots ADD COLUMN source_locator TEXT",
        [],
    )
    .expect("add physical provenance column");
    drop(con);
    verify(&path).expect("additional provenance is not public contract");

    let con = Connection::open(&path).expect("reopen fixture");
    con.execute(
        "UPDATE reference_contract_meta SET value='2' WHERE key='contract_major'",
        [],
    )
    .expect("tamper contract major");
    drop(con);
    let error = verify(&path).expect_err("metadata major mismatch must fail closed");
    assert!(error.to_string().contains("contract"));
    fs::remove_file(path).ok();
}

#[test]
fn verifier_rejects_noncanonical_contract_major_metadata() {
    for (label, major) in [("leading-zero", "01"), ("explicit-plus", "+1")] {
        let path = reference_db(&format!("reference-verifier-major-{label}"));
        let con = Connection::open(&path).expect("open fixture");
        con.execute(
            "UPDATE reference_contract_meta SET value=?1 WHERE key='contract_major'",
            [major],
        )
        .expect("tamper contract major representation");
        drop(con);
        let error = verify(&path).expect_err("noncanonical contract major must fail closed");
        assert!(error.to_string().contains("contract"));
        fs::remove_file(path).ok();
    }
}

#[test]
fn verifier_rejects_semantic_materialization_count_and_malformed_payload() {
    let count_path = reference_db("reference-verifier-semantic-count");
    let con = Connection::open(&count_path).expect("open fixture");
    con.execute(
        "UPDATE reference_semantic_expectations SET expected_fact_count=2 WHERE criterion_rule_id=7",
        [],
    )
    .expect("tamper semantic count");
    drop(con);
    let error = verify(&count_path).expect_err("materialization mismatch must fail closed");
    assert!(error.to_string().contains("semantic"));
    fs::remove_file(count_path).ok();

    let payload_path = reference_db("reference-verifier-semantic-payload");
    let con = Connection::open(&payload_path).expect("open fixture");
    con.execute(
        "UPDATE reference_criterion_semantics SET evaluator_kind='minimum_separation', semantic_role='applicability_condition', evaluation_mode='runtime_evaluable', fallback_action='review_required', structured_payload_json='{}' WHERE criterion_rule_id=7",
        [],
    )
    .expect("tamper semantic payload");
    drop(con);
    let error = verify(&payload_path).expect_err("malformed known evaluator must fail closed");
    assert!(error.to_string().contains("semantic"));
    fs::remove_file(payload_path).ok();
}

#[test]
fn verifier_rejects_foreign_key_violations() {
    let path = reference_db("reference-verifier-foreign-key");
    let con = Connection::open(&path).expect("open fixture");
    con.execute_batch(
        "PRAGMA foreign_keys=OFF;
         INSERT INTO product_identifiers(item_seq,system,value) VALUES('MISSING','fixture','x');
         PRAGMA foreign_keys=ON;",
    )
    .expect("create orphan fixture row");
    drop(con);
    let error = verify(&path).expect_err("foreign-key violation must fail closed");
    assert!(error.to_string().contains("foreign"));
    fs::remove_file(path).ok();
}

#[test]
fn verifier_rejects_contract_and_dataset_identity_inputs_outside_supported_window() {
    let path = reference_db("reference-verifier-inputs");
    let error = verify_reference_database(&path, 2, DATASET_ID)
        .expect_err("unsupported contract major must fail");
    assert!(error.to_string().contains("contract"));

    let error = verify_reference_database(&path, CONTRACT_MAJOR, "sha256:not-a-dataset")
        .expect_err("malformed dataset identity must fail");
    assert!(error.to_string().contains("dataset"));
    fs::remove_file(path).ok();
}
