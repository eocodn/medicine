use serde_json::{Map, Value};
use std::collections::BTreeSet;

use crate::safety_time::courses_overlap;

pub(crate) fn item(
    category: &str,
    label: &str,
    status: &str,
    summary: &str,
    details: Option<&str>,
    findings: Vec<Value>,
    qualifiers: Vec<Value>,
) -> Value {
    item_owned(
        category,
        label,
        status,
        summary.to_owned(),
        details.map(str::to_owned),
        findings,
        qualifiers,
    )
}

pub(crate) fn item_owned(
    category: &str,
    label: &str,
    status: &str,
    summary: String,
    details: Option<String>,
    findings: Vec<Value>,
    qualifiers: Vec<Value>,
) -> Value {
    let mut result = Map::new();
    result.insert("category".to_owned(), Value::String(category.to_owned()));
    result.insert("label".to_owned(), Value::String(label.to_owned()));
    result.insert("status".to_owned(), Value::String(status.to_owned()));
    result.insert("summary".to_owned(), Value::String(summary));
    result.insert("findings".to_owned(), Value::Array(findings));
    if let Some(details) = details.filter(|value| !value.is_empty()) {
        result.insert("details".to_owned(), Value::String(details));
    }
    if !qualifiers.is_empty() {
        result.insert("qualifiers".to_owned(), Value::Array(qualifiers));
    }
    Value::Object(result)
}

pub(crate) fn parse_object(raw: &str, name: &str) -> Result<Map<String, Value>, String> {
    let value =
        serde_json::from_str::<Value>(raw).map_err(|_| format!("{name} must be valid JSON"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| format!("{name} must be a JSON object"))
}

pub(crate) fn object_field<'a>(
    input: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a Map<String, Value>, String> {
    input
        .get(key)
        .and_then(Value::as_object)
        .ok_or_else(|| format!("{key} must be a JSON object"))
}

pub(crate) fn object_array_field(
    input: &Map<String, Value>,
    key: &str,
) -> Result<Vec<Map<String, Value>>, String> {
    let values = input
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{key} must be an array"))?;
    values
        .iter()
        .map(|value| {
            value
                .as_object()
                .cloned()
                .ok_or_else(|| format!("{key} must contain JSON objects"))
        })
        .collect()
}

pub(crate) fn string_set_field(
    input: &Map<String, Value>,
    key: &str,
) -> Result<BTreeSet<String>, String> {
    let values = input
        .get(key)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{key} must be an array"))?;
    values
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| format!("{key} must contain strings"))
        })
        .collect()
}

pub(crate) fn current_mapping_issues(
    current: &[Map<String, Value>],
    candidate_course: &Map<String, Value>,
) -> Vec<(String, String)> {
    let mut issues = Vec::new();
    for medication in current {
        let product_matched = text(medication, "product_mapping_status") == Some("matched");
        let canonical_issues = medication
            .get("canonical_resolution_issues")
            .and_then(Value::as_object)
            .is_some_and(|issues| !issues.is_empty());
        if product_matched && !canonical_issues {
            continue;
        }
        if courses_overlap(medication, candidate_course) == Some(false) {
            continue;
        }
        let scope = if product_matched {
            "canonical DUR 상세 기준"
        } else {
            "MFDS ITEM_SEQ"
        };
        issues.push((
            nonempty_text(medication, "product_name")
                .unwrap_or("이름을 확인할 수 없는 복용약")
                .to_owned(),
            scope.to_owned(),
        ));
    }
    issues
}

pub(crate) fn current_mapping_issue_text(
    issues: &[(String, String)],
    category_label: &str,
) -> (String, String) {
    if let [issue] = issues {
        let (name, scope) = issue;
        return (
            format!("{name} 확인 필요"),
            format!(
                "{name}의 {scope} DUR 연결을 확인하지 못해 {category_label}를 완전히 비교하지 못했습니다."
            ),
        );
    }
    let named = issues
        .iter()
        .map(|(name, scope)| format!("{name} ({scope})"))
        .collect::<Vec<_>>()
        .join(", ");
    (
        format!("현재 복용약 {}개 확인 필요", issues.len()),
        format!(
            "DUR 연결을 확인하지 못한 현재 복용약: {named}. {category_label}를 완전히 비교하지 못했습니다."
        ),
    )
}

pub(crate) fn clear_summary(category: &str) -> Option<&'static str> {
    match category {
        "combination_contraindication" => Some("병용금기 없음"),
        "age_contraindication" => Some("연령금기 해당 없음"),
        "pregnancy_contraindication" => Some("임부금기 해당 없음"),
        "elderly_caution" => Some("노인주의 해당 없음"),
        "therapeutic_duplication_caution" => Some("중복 없음"),
        _ => None,
    }
}

pub(crate) fn scalar_text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        Value::Null => "None".to_owned(),
        _ => value.to_string(),
    }
}

pub(crate) fn nonempty_text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    text(row, key).filter(|value| !value.is_empty())
}

pub(crate) fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}
