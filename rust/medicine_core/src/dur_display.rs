use chrono::NaiveDate;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

use crate::dur_display_support::{
    clear_summary, current_mapping_issue_text, current_mapping_issues, item, nonempty_text,
    object_array_field, object_field, parse_object, string_set_field, text,
};
use crate::dur_product_flags::{apply_product_flag_fallbacks, build_product_flag_checks};
use crate::dur_quantitative_display::{
    category_issue, mapping_complete, mapping_reason, quantitative_item,
};
use crate::safety_time::{age_years, parse_optional_date};

const DUR_CATEGORIES: [(&str, &str); 7] = [
    ("combination_contraindication", "병용금기"),
    ("age_contraindication", "연령금기"),
    ("pregnancy_contraindication", "임부금기"),
    ("elderly_caution", "노인주의"),
    ("dose_caution", "용량주의"),
    ("duration_caution", "투여기간주의"),
    ("therapeutic_duplication_caution", "효능군 중복주의"),
];

const INTERACTION_CATEGORIES: [&str; 2] = [
    "combination_contraindication",
    "therapeutic_duplication_caution",
];

#[derive(Debug)]
enum DisplayError {
    BadRequest(String),
    Internal,
}

impl From<String> for DisplayError {
    fn from(value: String) -> Self {
        Self::BadRequest(value)
    }
}

impl From<()> for DisplayError {
    fn from(_: ()) -> Self {
        Self::Internal
    }
}

pub(crate) fn inspect(input_json: &str) -> (u16, Value) {
    match build(input_json) {
        Ok(body) => (200, body),
        Err(DisplayError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(DisplayError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn build(input_json: &str) -> Result<Value, DisplayError> {
    let input = parse_object(input_json, "input")?;
    let person = object_field(&input, "person")?;
    let current = object_array_field(&input, "current")?;
    let risks = object_array_field(&input, "risks")?;
    let duration = object_field(&input, "duration")?;
    let dose = object_field(&input, "dose")?;
    let coverage = object_field(&input, "coverage")?;
    let dataset = object_field(&input, "dataset")?;
    let candidate_course = object_field(&input, "candidate_course")?;
    let product = object_field(&input, "product")?;
    let detailed_categories = string_set_field(&input, "detailed_product_categories")?;
    let review_items = input
        .get("review_items")
        .and_then(Value::as_array)
        .ok_or_else(|| DisplayError::BadRequest("review_items must be an array".to_owned()))?;
    let as_of = parse_optional_date(input.get("as_of").and_then(Value::as_str))
        .map_err(|_| DisplayError::BadRequest("as_of must be YYYY-MM-DD".to_owned()))?;

    let mut checks = build_checks(
        person,
        &current,
        &risks,
        duration,
        dose,
        coverage,
        dataset,
        candidate_course,
        as_of,
    )?;
    apply_product_flag_fallbacks(&mut checks, product, person, &detailed_categories, as_of)?;
    checks.extend(build_product_flag_checks(product));
    let requires_review = !review_items.is_empty()
        || checks.iter().any(|item| {
            item.get("status")
                .and_then(Value::as_str)
                .is_some_and(|status| matches!(status, "hit" | "conditional" | "unknown"))
        });
    Ok(json!({
        "dur_checks": checks,
        "requires_review": requires_review,
    }))
}

#[allow(clippy::too_many_arguments)]
fn build_checks(
    person: &Map<String, Value>,
    current: &[Map<String, Value>],
    risks: &[Map<String, Value>],
    duration: &Map<String, Value>,
    dose: &Map<String, Value>,
    coverage: &Map<String, Value>,
    dataset: &Map<String, Value>,
    candidate_course: &Map<String, Value>,
    as_of: Option<NaiveDate>,
) -> Result<Vec<Value>, DisplayError> {
    let mut by_category: BTreeMap<String, Vec<Map<String, Value>>> = BTreeMap::new();
    for risk in risks {
        let category = text(risk, "type").unwrap_or("").to_owned();
        by_category.entry(category).or_default().push(risk.clone());
    }
    let birth_date = text(person, "birth_date").ok_or(DisplayError::Internal)?;
    let current_age = age_years(birth_date, as_of).map_err(|_| DisplayError::Internal)?;
    let dataset_verified = text(dataset, "status") == Some("verified");
    let current_mapping_issues = current_mapping_issues(current, candidate_course);
    let mut result = Vec::with_capacity(DUR_CATEGORIES.len());

    for (category, label) in DUR_CATEGORIES {
        let findings = by_category.get(category).cloned().unwrap_or_default();
        let hit_findings = findings
            .iter()
            .filter(|finding| {
                matches!(text(finding, "severity"), Some("danger" | "warning"))
                    && !matches!(
                        text(finding, "evaluation_status"),
                        Some("unknown" | "conditional")
                    )
            })
            .cloned()
            .collect::<Vec<_>>();
        let conditional_findings = findings
            .iter()
            .filter(|finding| text(finding, "evaluation_status") == Some("conditional"))
            .cloned()
            .collect::<Vec<_>>();
        let unresolved_findings = findings
            .iter()
            .filter(|finding| {
                let is_hit = matches!(text(finding, "severity"), Some("danger" | "warning"))
                    && !matches!(
                        text(finding, "evaluation_status"),
                        Some("unknown" | "conditional")
                    );
                let is_conditional = text(finding, "evaluation_status") == Some("conditional");
                !is_hit && !is_conditional
            })
            .cloned()
            .collect::<Vec<_>>();

        if let Some(first) = hit_findings.first() {
            let summary = text(first, "title")
                .map(str::to_owned)
                .unwrap_or_else(|| format!("{label} 주의사항 있음"));
            result.push(item(
                category,
                label,
                "hit",
                &summary,
                nonempty_text(first, "details"),
                findings.iter().cloned().map(Value::Object).collect(),
                Vec::new(),
            ));
            continue;
        }
        if profile_not_applicable(category, person, current_age) {
            result.push(item(
                category,
                label,
                "not_applicable",
                "해당사항 없음",
                None,
                Vec::new(),
                Vec::new(),
            ));
            continue;
        }
        if let Some(first) = conditional_findings.first() {
            let summary = text(first, "title")
                .map(str::to_owned)
                .unwrap_or_else(|| format!("{label} 조건 확인 필요"));
            result.push(item(
                category,
                label,
                "conditional",
                &summary,
                Some(
                    nonempty_text(first, "details")
                        .unwrap_or("규칙의 적용 조건을 확인해야 합니다."),
                ),
                conditional_findings
                    .iter()
                    .cloned()
                    .map(Value::Object)
                    .collect(),
                Vec::new(),
            ));
            continue;
        }
        if category == "dose_caution" {
            result.push(quantitative_item(
                category,
                label,
                dose,
                mapping_complete(category, coverage),
                &mapping_reason(category, coverage),
                dataset_verified,
            ));
            continue;
        }
        if category == "duration_caution" {
            result.push(quantitative_item(
                category,
                label,
                duration,
                mapping_complete(category, coverage),
                &mapping_reason(category, coverage),
                dataset_verified,
            ));
            continue;
        }
        if let Some(first) = unresolved_findings.first() {
            let details = nonempty_text(first, "details")
                .or_else(|| nonempty_text(first, "title"))
                .unwrap_or("자동 판정하지 못했습니다.");
            result.push(item(
                category,
                label,
                "unknown",
                "확인 필요",
                Some(details),
                findings.iter().cloned().map(Value::Object).collect(),
                Vec::new(),
            ));
            continue;
        }
        if let Some(issue) = category_issue(category, coverage) {
            result.push(item(
                category,
                label,
                "unknown",
                "확인 필요",
                Some(&issue),
                Vec::new(),
                Vec::new(),
            ));
            continue;
        }
        if !dataset_verified {
            result.push(item(
                category,
                label,
                "unknown",
                "자동 확인 제한",
                Some("필수 DUR 원본을 검증하지 못해 확인할 수 없습니다."),
                Vec::new(),
                Vec::new(),
            ));
            continue;
        }
        if !mapping_complete(category, coverage) {
            let reason = mapping_reason(category, coverage);
            result.push(item(
                category,
                label,
                "unknown",
                "자동 확인 제한",
                Some(&reason),
                Vec::new(),
                Vec::new(),
            ));
            continue;
        }
        if INTERACTION_CATEGORIES.contains(&category) && !current_mapping_issues.is_empty() {
            let (summary, details) = current_mapping_issue_text(&current_mapping_issues, label);
            result.push(item(
                category,
                label,
                "unknown",
                &summary,
                Some(&details),
                Vec::new(),
                Vec::new(),
            ));
            continue;
        }
        result.push(item(
            category,
            label,
            "clear",
            clear_summary(category).ok_or(DisplayError::Internal)?,
            None,
            Vec::new(),
            Vec::new(),
        ));
    }
    Ok(result)
}

fn profile_not_applicable(category: &str, person: &Map<String, Value>, current_age: i32) -> bool {
    if category == "pregnancy_contraindication" {
        return text(person, "sex") == Some("male")
            || matches!(
                text(person, "pregnancy_status"),
                Some("not_pregnant" | "not_applicable")
            );
    }
    category == "elderly_caution" && current_age < 65
}
