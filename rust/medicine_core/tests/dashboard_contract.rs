mod common;

use medicine_core::{AccessClass, MedicineEngine};
use rusqlite::{params, Connection};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

fn temp_path(label: &str) -> PathBuf {
    common::temp_sqlite_path(&format!("dashboard-{label}"))
}

fn reference_db() -> PathBuf {
    let path = temp_path("reference");
    let con = Connection::open(&path).expect("create dashboard reference fixture");
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
    .expect("create dashboard reference schema");
    drop(con);
    path
}

fn personal_db() -> PathBuf {
    let path = temp_path("personal");
    let con = Connection::open(&path).expect("create dashboard personal fixture");
    con.execute_batch(
        r#"
        PRAGMA foreign_keys=ON;
        CREATE TABLE people(
            id TEXT PRIMARY KEY,name TEXT NOT NULL,birth_date TEXT NOT NULL,sex TEXT NOT NULL,
            pregnancy_status TEXT NOT NULL,lactation_status TEXT NOT NULL,notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE medications(
            id TEXT PRIMARY KEY,person_id TEXT NOT NULL, catalog_item_seq TEXT, product_code TEXT,
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
        CREATE TABLE dose_instances(
            id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,schedule_key TEXT NOT NULL,scheduled_time TEXT,
            slot_label TEXT,dose_text TEXT,product_name_snapshot TEXT,ingredient_name_snapshot TEXT,
            status TEXT NOT NULL DEFAULT 'planned',completed_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(medication_id,scheduled_date,schedule_key)
        );
        CREATE TABLE dose_logs(
            id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,note TEXT,dose_instance_id TEXT,product_name_snapshot TEXT,
            dosage_text_snapshot TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE prn_requests(
            request_id TEXT PRIMARY KEY,medication_id TEXT NOT NULL,person_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,dose_instance_id TEXT NOT NULL,state TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status)
            VALUES('p1','대시보드','1990-01-01','male','not_applicable','not_applicable');
        INSERT INTO medications(
            id,person_id,catalog_item_seq,product_code,product_name,ingredient_name,dosage_text,
            dose_amount,dose_unit,frequency_per_day,meal_relation,administration_route,
            prescription_days,long_term,start_date,end_date,active,revision
        ) VALUES
            ('early','p1','SAFE','SAFE','안전약','SafeDrug','1정',1,'정',1,'after_meal','oral',
             3,0,'2026-08-08','2026-08-10',1,1),
            ('late','p1','OTHER','OTHER','다른약','OtherDrug','1정',1,'정',1,'before_meal','oral',
             NULL,1,'2026-08-01',NULL,1,1),
            ('expired','p1','SAFE','SAFE','지난약','SafeDrug','1정',1,'정',1,'unspecified','oral',
             1,0,'2026-08-01','2026-08-09',1,1),
            ('inactive','p1','SAFE','SAFE','중단약','SafeDrug','1정',1,'정',1,'unspecified','oral',
             NULL,1,'2026-08-01',NULL,0,2);
        INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
            VALUES('early-schedule','early','08:00',NULL),('late-schedule','late','20:00',NULL);
        "#,
    )
    .expect("create dashboard personal schema");
    for index in 0..21 {
        con.execute(
            "INSERT INTO dose_logs(
                 id,medication_id,person_id,status,occurred_at,note,dose_instance_id,
                 product_name_snapshot,dosage_text_snapshot
             ) VALUES(?,?,?,?,?,?,?,?,?)",
            params![
                format!("log-{index:02}"),
                "early",
                "p1",
                "taken",
                format!("2026-08-10T00:00:{index:02}+09:00"),
                Option::<String>::None,
                Option::<String>::None,
                "안전약",
                "1정",
            ],
        )
        .expect("insert dashboard log");
    }
    drop(con);
    path
}

fn engine(reference: Option<&Path>, personal: &Path) -> MedicineEngine {
    MedicineEngine::new(reference, Some(personal), Some("update_required"))
}

fn get(engine: &MedicineEngine, path: &str) -> Value {
    serde_json::from_str(&engine.request("GET", path, "")).expect("decode dashboard response")
}

fn cleanup(reference: Option<PathBuf>, personal: PathBuf) {
    fs::remove_file(personal).ok();
    if let Some(reference) = reference {
        fs::remove_file(reference).ok();
    }
}

#[test]
fn medication_list_route_is_personal_read_and_never_materializes_daily_plan() {
    let reference = reference_db();
    let personal = personal_db();
    let engine = engine(Some(&reference), &personal);
    let path = "/api/people/p1/medications?date=2026-08-10";

    assert_eq!(
        engine.request_access("GET", path),
        AccessClass::PersonalRead
    );
    assert!(engine.handles_request("GET", path));

    let response = get(&engine, path);
    assert_eq!(response["status"], 200, "{response}");
    assert_eq!(
        response["body"]
            .as_array()
            .expect("medication list")
            .iter()
            .map(|medication| medication["id"].as_str().expect("medication id"))
            .collect::<Vec<_>>(),
        vec!["early", "late"]
    );
    assert_eq!(response["body"][0]["course_progress"]["status"], "active");
    assert!(response["body"][0]["current_assessment"].is_object());

    let con = Connection::open(&personal).expect("observe medication-list side effects");
    let count: i64 = con
        .query_row("SELECT COUNT(*) FROM dose_instances", [], |row| row.get(0))
        .expect("count dose instances");
    assert_eq!(count, 0);

    cleanup(Some(reference), personal);
}

#[test]
fn dashboard_route_is_rust_owned_personal_write_and_strips_query_for_matching() {
    let engine = MedicineEngine::new(None, None, None);
    let path = "/api/people/p1/dashboard?date=2026-08-10";

    assert_eq!(
        engine.request_access("GET", path),
        AccessClass::PersonalWrite
    );
    assert!(engine.handles_request("GET", path));
}

#[test]
fn dashboard_preserves_person_not_found_and_iso_date_validation() {
    let personal = personal_db();
    let engine = engine(None, &personal);

    let missing = get(&engine, "/api/people/missing/dashboard?date=2026-08-10");
    assert_eq!(missing["status"], 404);
    assert_eq!(missing["body"]["detail"], "person not found");

    let invalid = get(&engine, "/api/people/p1/dashboard?date=not-a-date");
    assert_eq!(invalid["status"], 400);
    assert_eq!(
        invalid["body"]["detail"],
        "Invalid isoformat string: 'not-a-date'"
    );

    cleanup(None, personal);
}

#[test]
fn dashboard_filters_expired_and_inactive_courses_and_sorts_timed_medications() {
    let reference = reference_db();
    let personal = personal_db();
    let engine = engine(Some(&reference), &personal);

    let dashboard = get(&engine, "/api/people/p1/dashboard?date=2026-08-10");
    assert_eq!(dashboard["status"], 200);
    let medications = dashboard["body"]["medications"]
        .as_array()
        .expect("dashboard medication array");
    assert_eq!(
        medications
            .iter()
            .map(|medication| medication["id"].as_str().expect("medication id"))
            .collect::<Vec<_>>(),
        vec!["early", "late"]
    );
    assert_eq!(medications[0]["course_progress"]["status"], "active");
    assert_eq!(medications[0]["course_progress"]["remaining_days"], 0);
    assert_eq!(medications[1]["course_progress"], Value::Null);

    cleanup(Some(reference), personal);
}

#[test]
fn dashboard_current_assessment_excludes_self_and_exposes_derived_badges() {
    let reference = reference_db();
    let personal = personal_db();
    let engine = engine(Some(&reference), &personal);

    let dashboard = get(&engine, "/api/people/p1/dashboard?date=2026-08-10");
    let medication = &dashboard["body"]["medications"][0];
    let assessment = &medication["current_assessment"];
    assert!(assessment.get("draft_fingerprint").is_none());
    assert!(assessment.get("warning_token").is_none());
    assert_eq!(assessment["review_items"], serde_json::json!([]));
    assert!(!assessment
        .get("review_items")
        .and_then(Value::as_array)
        .expect("review items")
        .iter()
        .any(|item| item["related_medication_id"] == "early"));
    assert_eq!(medication["permit_status"], "active");
    assert_eq!(medication["permit_status_name"], "정상");
    assert!(medication["permit_status_changed_at"].is_null());
    assert_eq!(medication["dur_alert"], false);
    assert_eq!(medication["split_prohibited"], false);
    assert_eq!(medication["dur_review_required"], false, "{dashboard}");
    assert_eq!(medication["review_required"], false);

    cleanup(Some(reference), personal);
}

#[test]
fn dashboard_rejects_blank_date_and_uses_the_last_query_value() {
    let reference = reference_db();
    let personal = personal_db();
    let engine = engine(Some(&reference), &personal);

    for path in [
        "/api/people/p1/dashboard?date",
        "/api/people/p1/dashboard?date=",
        "/api/people/p1/dashboard?date=2026-08-10&date=",
    ] {
        let response = get(&engine, path);
        assert_eq!(response["status"], 400, "{path}: {response}");
        assert_eq!(
            response["body"]["detail"], "Invalid isoformat string: ''",
            "{path}: {response}"
        );
    }

    cleanup(Some(reference), personal);
}

#[test]
fn dashboard_keeps_local_state_and_daily_plan_when_reference_is_unavailable() {
    let reference = reference_db();
    let personal = personal_db();
    let mut engine = engine(Some(&reference), &personal);
    engine
        .set_reference_available(false, Some("update_required"))
        .expect("retire dashboard reference");

    let dashboard = get(&engine, "/api/people/p1/dashboard?date=2026-08-10");
    assert_eq!(dashboard["status"], 200);
    assert_eq!(
        dashboard["body"]["medications"].as_array().unwrap().len(),
        2
    );
    assert!(dashboard["body"]["medications"]
        .as_array()
        .unwrap()
        .iter()
        .all(|medication| !medication
            .as_object()
            .unwrap()
            .contains_key("current_assessment")));
    assert_eq!(dashboard["body"]["daily_plan"]["date"], "2026-08-10");
    assert_eq!(
        dashboard["body"]["daily_plan"]["doses"]
            .as_array()
            .unwrap()
            .len(),
        2
    );

    cleanup(Some(reference), personal);
}

#[test]
fn dashboard_returns_the_20_newest_logs_in_authoritative_order() {
    let personal = personal_db();
    let engine = engine(None, &personal);

    let dashboard = get(&engine, "/api/people/p1/dashboard?date=2026-08-10");
    let logs = dashboard["body"]["recent_logs"]
        .as_array()
        .expect("recent log array");
    assert_eq!(logs.len(), 20);
    assert_eq!(logs[0]["id"], "log-20");
    assert_eq!(logs[19]["id"], "log-01");
    assert!(logs.windows(2).all(|pair| {
        pair[0]["occurred_at"].as_str().unwrap() > pair[1]["occurred_at"].as_str().unwrap()
    }));
    assert!(!logs[0].as_object().unwrap().contains_key("request_id"));

    cleanup(None, personal);
}

#[test]
fn dashboard_daily_plan_materialization_is_idempotent() {
    let personal = personal_db();
    let engine = engine(None, &personal);
    let path = "/api/people/p1/dashboard?date=2026-08-10";

    let first = get(&engine, path);
    let first_ids = first["body"]["daily_plan"]["doses"]
        .as_array()
        .unwrap()
        .iter()
        .map(|dose| dose["id"].as_str().unwrap().to_owned())
        .collect::<Vec<_>>();
    let second = get(&engine, path);
    let second_ids = second["body"]["daily_plan"]["doses"]
        .as_array()
        .unwrap()
        .iter()
        .map(|dose| dose["id"].as_str().unwrap().to_owned())
        .collect::<Vec<_>>();
    assert_eq!(second_ids, first_ids);

    let con = Connection::open(&personal).expect("observe materialized doses");
    let count: i64 = con
        .query_row("SELECT COUNT(*) FROM dose_instances", [], |row| row.get(0))
        .expect("count materialized doses");
    assert_eq!(count, 2);

    cleanup(None, personal);
}

#[test]
fn dashboard_uses_target_date_for_legacy_long_term_medication_without_start_date() {
    let reference = reference_db();
    let personal = personal_db();
    let con = Connection::open(&personal).expect("open legacy dashboard fixture");
    con.execute(
        "INSERT INTO medications(
             id,person_id,catalog_item_seq,product_code,product_name,ingredient_name,dosage_text,
             dose_amount,dose_unit,frequency_per_day,meal_relation,administration_route,
             prescription_days,long_term,start_date,end_date,active,revision
         ) VALUES('legacy','p1','SAFE','SAFE','구형 장기약','SafeDrug','1정',1,'정',NULL,
                  'unspecified','oral',NULL,1,NULL,NULL,1,1)",
        [],
    )
    .expect("insert legacy medication without start date");
    drop(con);
    let engine = engine(Some(&reference), &personal);

    let dashboard = get(&engine, "/api/people/p1/dashboard?date=2026-08-10");
    assert_eq!(dashboard["status"], 200, "{dashboard}");
    let legacy = dashboard["body"]["medications"]
        .as_array()
        .unwrap()
        .iter()
        .find(|medication| medication["id"] == "legacy")
        .expect("legacy medication remains visible");
    assert!(legacy["current_assessment"].is_object());

    cleanup(Some(reference), personal);
}
