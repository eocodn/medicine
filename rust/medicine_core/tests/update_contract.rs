mod common;

use medicine_core::{AccessClass, MedicineEngine};
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

fn temp_path(label: &str) -> PathBuf {
    common::temp_sqlite_path(&format!("update-{label}"))
}

fn reference_db() -> PathBuf {
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

fn personal_db() -> PathBuf {
    let path = temp_path("personal");
    let con = Connection::open(&path).expect("create personal fixture");
    con.execute_batch(
        r#"
        PRAGMA foreign_keys=ON;
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
            id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,time_of_day TEXT NOT NULL,
            dose_text TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE medication_revisions(
            medication_id TEXT NOT NULL,revision INTEGER NOT NULL,action TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,assessment_json TEXT,acknowledged INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,payload_hash TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id,revision)
        );
        CREATE TABLE dose_instances(
            id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,schedule_key TEXT NOT NULL,scheduled_time TEXT,
            slot_label TEXT,dose_text TEXT,product_name_snapshot TEXT,ingredient_name_snapshot TEXT,
            status TEXT NOT NULL DEFAULT 'planned',completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status) VALUES
            ('adult','성인','1990-01-01','male','not_applicable','not_applicable'),
            ('manual-person','수기약 사용자','1990-01-01','male','not_applicable','not_applicable'),
            ('inactive-person','취소약 사용자','1990-01-01','male','not_applicable','not_applicable'),
            ('child','소아','2015-01-01','male','not_applicable','not_applicable');
        INSERT INTO medications(
            id,person_id,catalog_item_seq,product_code,product_name,ingredient_name,manufacturer,
            catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,meal_relation,
            administration_route,as_needed,prn_max_per_day,prescription_days,long_term,start_date,
            active,source,revision
        ) VALUES
            ('m1','adult','SAFE','SAFE','안전약','SafeDrug','제약','canonical','1정',1,'정',2,
             'after_meal','oral',0,NULL,7,0,'2026-08-20',1,'dur_search',4),
            ('manual-1','manual-person',NULL,NULL,'수기약','성분','', 'manual','복용',1,'회',1,
             'unspecified','unknown',0,NULL,NULL,1,'2026-08-20',1,'manual',1),
            ('inactive-1','inactive-person','INACTIVE','INACTIVE','취소약','OldDrug','제약','canonical','1정',1,
             '정',1,'unspecified','oral',0,NULL,7,0,'2026-08-20',1,'dur_search',2);
        INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text) VALUES
            ('m1-s1','m1','08:00','1정'),('m1-s2','m1','20:00','1정'),
            ('manual-s1','manual-1','09:00','복용'),('inactive-s1','inactive-1','08:00','1정');
        INSERT INTO dose_instances(id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,status) VALUES
            ('past-planned','m1','adult','2020-01-01','slot:08:00','08:00','planned'),
            ('future-planned','m1','adult','2099-01-01','slot:08:00','08:00','planned'),
            ('future-completed','m1','adult','2099-01-01','slot:20:00','20:00','taken');
        "#,
    )
    .expect("create personal schema");

    for (medication_id, revision, action) in [
        ("m1", 1, "create"),
        ("m1", 2, "update"),
        ("m1", 3, "update"),
        ("m1", 4, "update"),
        ("manual-1", 1, "create"),
        ("inactive-1", 1, "create"),
        ("inactive-1", 2, "update"),
    ] {
        let snapshot = json!({"id":medication_id,"revision":revision,"action":action});
        con.execute(
            "INSERT INTO medication_revisions(medication_id,revision,action,snapshot_json,assessment_json,acknowledged,request_id,payload_hash) VALUES(?,?,?,?,?,?,?,?)",
            params![medication_id, revision, action, snapshot.to_string(), json!({"requires_review":false}).to_string(), 0, Option::<String>::None, "fixture-hash"],
        )
        .expect("insert revision fixture");
    }
    drop(con);
    path
}

fn fixture() -> (PathBuf, PathBuf, MedicineEngine) {
    let reference = reference_db();
    let personal = personal_db();
    let engine = MedicineEngine::new(Some(&reference), Some(&personal), None);
    (reference, personal, engine)
}

fn response(engine: &MedicineEngine, body: Value) -> Value {
    serde_json::from_str(&engine.request("PATCH", "/api/medications/m1", &body.to_string()))
        .expect("decode PATCH response")
}

fn row(path: &Path, sql: &str) -> Vec<Value> {
    let con = Connection::open(path).expect("open personal fixture");
    let mut statement = con.prepare(sql).expect("prepare state query");
    statement
        .query_map([], |item| {
            Ok(json!({
                "id": item.get::<_, String>(0)?,
                "status": item.get::<_, String>(1)?,
            }))
        })
        .expect("query state")
        .collect::<Result<Vec<_>, _>>()
        .expect("collect state")
}

fn cleanup(reference: PathBuf, personal: PathBuf) {
    fs::remove_file(reference).ok();
    fs::remove_file(personal).ok();
}

#[test]
fn patch_route_is_owned_and_classified_as_personal_write() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/m1"));
    assert_eq!(
        engine.request_access("PATCH", "/api/medications/m1?expected_revision=4"),
        AccessClass::PersonalWrite
    );
    assert!(!engine.handles_request("PATCH", "/api/medications/m1/history"));

    let unavailable = MedicineEngine::new(None, Some(&personal), Some("update_required"));
    let blocked: Value = serde_json::from_str(&unavailable.request(
        "PATCH",
        "/api/medications/m1",
        &json!({"expected_revision":4,"dose_amount":2}).to_string(),
    ))
    .expect("decode reference unavailable response");
    assert_eq!(blocked["status"], 503);
    assert_eq!(blocked["body"]["reference_status"], "update_required");
    cleanup(reference, personal);
}

#[test]
fn patch_rejects_missing_revision_and_unknown_fields_without_mutation() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/m1"));

    let missing = response(&engine, json!({"dose_amount":2}));
    assert_eq!(missing["status"], 400);
    assert!(missing["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("expected_revision"));

    let unknown = response(&engine, json!({"expected_revision":4,"not_a_field":true}));
    assert_eq!(unknown["status"], 400);
    assert!(unknown["body"]["detail"]
        .as_str()
        .unwrap()
        .contains("unknown"));

    let con = Connection::open(&personal).expect("open unchanged fixture");
    let state: (i64, f64, String) = con
        .query_row(
            "SELECT revision,dose_amount,product_name FROM medications WHERE id='m1'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .expect("read unchanged medication");
    assert_eq!(state, (4, 1.0, "안전약".to_owned()));
    cleanup(reference, personal);
}

#[test]
fn patch_uses_revision_cas_and_partial_merge() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/m1"));

    let stale = response(&engine, json!({"expected_revision":3,"dose_amount":2}));
    assert_eq!(stale["status"], 409);
    assert_eq!(
        stale["body"]["detail"],
        "expected revision 3, current revision is 4"
    );

    let updated = response(&engine, json!({"expected_revision":4,"dose_amount":2}));
    assert_eq!(updated["status"], 200, "{updated}");
    assert_eq!(updated["body"]["revision"], 5);
    assert_eq!(updated["body"]["dose_amount"], 2.0);
    assert_eq!(updated["body"]["frequency_per_day"], 2);
    assert_eq!(updated["body"]["meal_relation"], "after_meal");
    assert_eq!(updated["body"]["schedules"][0]["time_of_day"], "08:00");

    let history = engine.request("GET", "/api/medications/m1/history", "");
    let history: Value = serde_json::from_str(&history).expect("decode history");
    assert_eq!(history["status"], 200);
    let revisions = history["body"].as_array().expect("history array");
    assert_eq!(revisions.last().unwrap()["revision"], 5);
    assert_eq!(revisions.last().unwrap()["action"], "update");
    cleanup(reference, personal);
}

#[test]
fn patch_requires_exact_warning_token_and_preserves_manual_identity() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/manual-1"));

    let blocked = serde_json::from_str::<Value>(&engine.request(
        "PATCH",
        "/api/medications/manual-1",
        &json!({"expected_revision":1,"dose_amount":2}).to_string(),
    ))
    .expect("decode manual confirmation");
    assert_eq!(blocked["status"], 409);
    assert_eq!(blocked["body"]["confirmation_required"], true);
    let token = blocked["body"]["warning_token"]
        .as_str()
        .expect("warning token")
        .to_owned();

    for supplied in ["wrong".to_owned(), format!(" {token} ")] {
        let rejected = serde_json::from_str::<Value>(&engine.request(
            "PATCH",
            "/api/medications/manual-1",
            &json!({"expected_revision":1,"dose_amount":2,"acknowledge_warnings":true,"warning_token":supplied}).to_string(),
        ))
        .expect("decode rejected confirmation");
        assert_eq!(rejected["status"], 409);
        assert_eq!(rejected["body"]["confirmation_required"], true);
    }

    let accepted = serde_json::from_str::<Value>(&engine.request(
        "PATCH",
        "/api/medications/manual-1",
        &json!({"expected_revision":1,"dose_amount":2,"acknowledge_warnings":true,"warning_token":token}).to_string(),
    ))
    .expect("decode accepted confirmation");
    assert_eq!(accepted["status"], 200);
    assert_eq!(accepted["body"]["catalog_source"], "manual");
    assert_eq!(accepted["body"]["source"], "manual");
    assert_eq!(accepted["body"]["product_name"], "수기약");
    assert_eq!(accepted["body"]["revision"], 2);
    cleanup(reference, personal);
}

#[test]
fn patch_deletes_only_future_planned_doses_and_supports_schedule_prn_transitions() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/m1"));

    let to_prn = response(
        &engine,
        json!({
            "expected_revision":4,
            "as_needed":true,
            "prn_max_per_day":3,
            "schedule_times":[]
        }),
    );
    assert_eq!(to_prn["status"], 200, "{to_prn}");
    assert_eq!(to_prn["body"]["as_needed"], true);
    assert_eq!(to_prn["body"]["prn_max_per_day"], 3);
    assert_eq!(
        to_prn["body"]["schedules"].as_array().map(Vec::len),
        Some(0)
    );
    assert_eq!(
        row(
            &personal,
            "SELECT id,status FROM dose_instances WHERE medication_id='m1' ORDER BY id"
        ),
        vec![
            json!({"id":"future-completed","status":"taken"}),
            json!({"id":"past-planned","status":"planned"}),
        ]
    );

    let back_to_schedule = serde_json::from_str::<Value>(
        &engine.request(
            "PATCH",
            "/api/medications/m1",
            &json!({
                "expected_revision":5,
                "as_needed":false,
                "prn_max_per_day":null,
                "frequency_per_day":2,
                "schedule_times":["09:00","21:00"]
            })
            .to_string(),
        ),
    )
    .expect("decode schedule transition");
    assert_eq!(back_to_schedule["status"], 200);
    assert_eq!(back_to_schedule["body"]["as_needed"], false);
    assert_eq!(
        back_to_schedule["body"]["schedules"]
            .as_array()
            .map(Vec::len),
        Some(2)
    );
    assert_eq!(
        back_to_schedule["body"]["schedules"][1]["time_of_day"],
        "21:00"
    );
    cleanup(reference, personal);
}

#[test]
fn patch_allows_existing_medication_when_reference_permit_is_inactive() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/inactive-1"));
    let result = serde_json::from_str::<Value>(&engine.request(
        "PATCH",
        "/api/medications/inactive-1",
        &json!({"expected_revision":2,"dose_amount":2}).to_string(),
    ))
    .expect("decode inactive existing update");
    assert_eq!(result["status"], 200, "{result}");
    assert_eq!(result["body"]["catalog_item_seq"], "INACTIVE");
    assert_eq!(result["body"]["dose_amount"], 2.0);
    assert_eq!(result["body"]["revision"], 3);
    cleanup(reference, personal);
}

#[test]
fn patch_excludes_the_target_from_duplicate_regimen_assessment() {
    let (reference, personal, engine) = fixture();
    assert!(engine.handles_request("PATCH", "/api/medications/m1"));
    let result = response(&engine, json!({"expected_revision":4,"dose_amount":1}));
    assert_eq!(result["status"], 200, "{result}");
    let assessment = &result["body"]["assessment"];
    assert_eq!(assessment["requires_review"], false);
    let risks = assessment["risks"].as_array().cloned().unwrap_or_default();
    assert!(!risks
        .iter()
        .any(|risk| risk["type"] == "therapeutic_duplication_caution"));
    cleanup(reference, personal);
}
