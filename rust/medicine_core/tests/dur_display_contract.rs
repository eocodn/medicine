use medicine_core::assemble_dur_display;
use serde_json::{json, Value};

fn assemble(payload: Value) -> Value {
    serde_json::from_str(&assemble_dur_display(&payload.to_string()))
        .expect("decode DUR display response")
}

fn complete_coverage() -> Value {
    json!({
        "status": "complete",
        "product": {"status": "matched"},
        "category_resolution": {},
        "not_evaluable_checks": [],
    })
}

fn empty_quantitative(result: &str) -> Value {
    json!({"result": result, "source_scope": "canonical_product", "source_rows": []})
}

#[test]
fn quantitative_hit_and_split_flag_keep_python_display_contract() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "not_pregnant"
        },
        "current": [],
        "risks": [],
        "duration": {
            "result": "exceeded",
            "requested_days": 35,
            "maximum_days": 28,
            "source_scope": "canonical_product",
            "source_rows": []
        },
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2026-08-20", "end_date": "2026-09-23"},
        "product": {
            "product_flags": [{"category": "split_caution", "details": "분할 불가"}]
        },
        "detailed_product_categories": ["duration_caution"],
        "review_items": [],
        "as_of": "2026-08-22"
    }));
    assert_eq!(result["status"], 200);
    let checks = result["body"]["dur_checks"].as_array().expect("DUR checks");
    assert_eq!(checks.len(), 8);
    let duration = checks
        .iter()
        .find(|item| item["category"] == "duration_caution")
        .expect("duration check");
    assert_eq!(duration["status"], "hit");
    assert_eq!(duration["summary"], "투여기간주의 기준 초과");
    assert_eq!(duration["details"], "입력한 투여기간 35일 · DUR 기준 28일");
    let split = checks
        .iter()
        .find(|item| item["category"] == "split_caution")
        .expect("split check");
    assert_eq!(split["status"], "hit");
    assert_eq!(split["summary"], "분할불가");
    assert_eq!(result["body"]["requires_review"], true);
}

#[test]
fn overlapping_unmapped_current_medication_keeps_interactions_unknown() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable"
        },
        "current": [{
            "product_name": "확인필요약",
            "product_mapping_status": "not_matched",
            "canonical_resolution_issues": {},
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "active": true
        }],
        "risks": [],
        "duration": empty_quantitative("not_applicable"),
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2026-08-20", "end_date": "2026-08-25"},
        "product": {"product_flags": []},
        "detailed_product_categories": [],
        "review_items": [],
        "as_of": "2026-08-22"
    }));
    let checks = result["body"]["dur_checks"].as_array().expect("DUR checks");
    for category in [
        "combination_contraindication",
        "therapeutic_duplication_caution",
    ] {
        let check = checks
            .iter()
            .find(|item| item["category"] == category)
            .expect("interaction check");
        assert_eq!(check["status"], "unknown");
        assert_eq!(check["summary"], "확인필요약 확인 필요");
        assert!(check["details"]
            .as_str()
            .is_some_and(|value| value.contains("MFDS ITEM_SEQ")));
    }
    assert_eq!(result["body"]["requires_review"], true);
}

#[test]
fn non_overlapping_unmapped_medication_does_not_block_interaction_clear_state() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1990-01-01",
            "sex": "male",
            "pregnancy_status": "not_applicable"
        },
        "current": [{
            "product_name": "과거복용약",
            "product_mapping_status": "not_matched",
            "canonical_resolution_issues": {},
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "active": true
        }],
        "risks": [],
        "duration": empty_quantitative("not_applicable"),
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2026-08-20", "end_date": "2026-08-25"},
        "product": {"product_flags": []},
        "detailed_product_categories": [],
        "review_items": [],
        "as_of": "2026-08-22"
    }));
    let checks = result["body"]["dur_checks"].as_array().expect("DUR checks");
    for category in [
        "combination_contraindication",
        "therapeutic_duplication_caution",
    ] {
        let check = checks
            .iter()
            .find(|item| item["category"] == category)
            .expect("interaction check");
        assert_eq!(check["status"], "clear");
    }
}

#[test]
fn leap_day_birthday_uses_february_28_anniversary_for_elderly_flag() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1960-02-29",
            "sex": "female",
            "pregnancy_status": "not_pregnant"
        },
        "current": [],
        "risks": [],
        "duration": empty_quantitative("not_applicable"),
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2025-02-28"},
        "product": {"product_flags": [{"category": "elderly_caution"}]},
        "detailed_product_categories": [],
        "review_items": [],
        "as_of": "2025-02-28"
    }));
    let elderly = result["body"]["dur_checks"]
        .as_array()
        .expect("DUR checks")
        .iter()
        .find(|item| item["category"] == "elderly_caution")
        .expect("elderly check");
    assert_eq!(elderly["status"], "hit");
    assert_eq!(elderly["summary"], "노인주의 대상");
}

#[test]
fn product_flags_fail_closed_without_detailed_rules() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1950-01-01",
            "sex": "female",
            "pregnancy_status": "pregnant"
        },
        "current": [],
        "risks": [],
        "duration": empty_quantitative("not_applicable"),
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2026-08-20"},
        "product": {
            "product_flags": [
                {"category": "age_contraindication"},
                {"category": "pregnancy_contraindication"},
                {"category": "elderly_caution"},
                {"category": "dose_caution"},
                {"category": "duration_caution"}
            ]
        },
        "detailed_product_categories": [],
        "review_items": [],
        "as_of": "2026-08-22"
    }));
    let checks = result["body"]["dur_checks"].as_array().expect("DUR checks");
    let status = |category: &str| {
        checks
            .iter()
            .find(|item| item["category"] == category)
            .expect("category check")["status"]
            .as_str()
            .expect("status")
    };
    assert_eq!(status("age_contraindication"), "unknown");
    assert_eq!(status("pregnancy_contraindication"), "hit");
    assert_eq!(status("elderly_caution"), "hit");
    assert_eq!(status("dose_caution"), "unknown");
    assert_eq!(status("duration_caution"), "unknown");
    assert_eq!(result["body"]["requires_review"], true);
}

#[test]
fn definitive_and_conditional_findings_take_priority_over_clear_states() {
    let result = assemble(json!({
        "person": {
            "birth_date": "1990-01-01",
            "sex": "female",
            "pregnancy_status": "pregnant"
        },
        "current": [],
        "risks": [
            {
                "type": "pregnancy_contraindication",
                "severity": "danger",
                "title": "임부금기 · 2등급",
                "details": "임부 사용 시 위해 가능"
            },
            {
                "type": "age_contraindication",
                "severity": "info",
                "title": "연령금기 기준 확인 필요",
                "details": "전문가 확인 필요",
                "evaluation_status": "conditional"
            }
        ],
        "duration": empty_quantitative("not_applicable"),
        "dose": empty_quantitative("not_applicable"),
        "coverage": complete_coverage(),
        "dataset": {"status": "verified"},
        "candidate_course": {"start_date": "2026-08-20"},
        "product": {"product_flags": []},
        "detailed_product_categories": [],
        "review_items": [],
        "as_of": "2026-08-22"
    }));
    let checks = result["body"]["dur_checks"].as_array().expect("DUR checks");
    let pregnancy = checks
        .iter()
        .find(|item| item["category"] == "pregnancy_contraindication")
        .expect("pregnancy check");
    assert_eq!(pregnancy["status"], "hit");
    assert_eq!(pregnancy["summary"], "임부금기 · 2등급");
    let age = checks
        .iter()
        .find(|item| item["category"] == "age_contraindication")
        .expect("age check");
    assert_eq!(age["status"], "conditional");
    assert_eq!(age["summary"], "연령금기 기준 확인 필요");
}
