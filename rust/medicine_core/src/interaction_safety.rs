use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::canonical_products::{self, infer_administration_route, ProductError};
use crate::interaction_timing::{interaction_timing_applies, remark_timing};
use crate::reference_runtime::{
    combination_rows, resolved_product_rows, unlinked_combination_exists,
};
use crate::reference_semantics::{
    criterion_note_requires_review, dedupe_qualifiers, qualifiers, semantic_records,
};
use crate::safety_time::courses_overlap;

#[derive(Debug)]
enum InteractionError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

#[derive(Default)]
struct DuplicationGroup {
    requires_review: bool,
    qualifiers: Vec<Value>,
}

pub(crate) fn inspect(
    canonical_db: Option<&Path>,
    product_ref: &str,
    current_json: &str,
    candidate_course_json: &str,
) -> (u16, Value) {
    match inspect_inner(
        canonical_db,
        product_ref,
        current_json,
        candidate_course_json,
    ) {
        Ok(body) => (200, body),
        Err(InteractionError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(InteractionError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(InteractionError::Unavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(InteractionError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn inspect_inner(
    canonical_db: Option<&Path>,
    product_ref: &str,
    current_json: &str,
    candidate_course_json: &str,
) -> Result<Value, InteractionError> {
    let con = canonical_products::open(canonical_db).map_err(InteractionError::from)?;
    let product = canonical_products::resolve_from_connection(&con, product_ref)
        .map_err(InteractionError::from)?;
    let current = parse_object_array(current_json, "current")?;
    let candidate_course = parse_object(candidate_course_json, "candidate_course")?;
    let risks = evaluate(&con, &product, &current, &candidate_course)
        .map_err(|_| InteractionError::Internal)?;
    Ok(json!({"product": product, "risks": risks}))
}

pub(crate) fn evaluate(
    con: &Connection,
    product: &Value,
    current: &[Map<String, Value>],
    candidate_course: &Map<String, Value>,
) -> Result<Vec<Value>, ()> {
    let product = product.as_object().ok_or(())?;
    let mut risks = combination_risks(con, product, current, candidate_course)?;
    risks.extend(duplication_risks(con, product, current, candidate_course)?);
    Ok(dedupe_and_sort(risks))
}

fn combination_risks(
    con: &Connection,
    product: &Map<String, Value>,
    current: &[Map<String, Value>],
    candidate_course: &Map<String, Value>,
) -> Result<Vec<Value>, ()> {
    let Some(target) = item_seq(product) else {
        return Ok(Vec::new());
    };
    let mut risks = Vec::new();
    for medication in current {
        let Some(paired) = item_seq(medication) else {
            continue;
        };
        let rows = combination_rows(con, target, paired)?;
        for row in &rows {
            let candidate_side = if text(row, "item_seq") == Some(target) {
                "left"
            } else {
                "right"
            };
            let original_details = canonical_details(row);
            let qualifier_values = qualifiers(con, row)?;
            let qualifier_review = criterion_note_requires_review(con, row)?;
            let timing_value = remark_timing(con, row, original_details.as_deref())?;
            let timing = timing_value.as_object().ok_or(())?;
            if !interaction_timing_applies(timing, candidate_course, medication, candidate_side) {
                continue;
            }
            let details = if qualifier_review {
                Some(details_with_professional_review(
                    original_details.as_deref(),
                ))
            } else {
                original_details
            };
            let mut message =
                details.unwrap_or_else(|| "DUR 병용금기 조합에 해당합니다.".to_owned());
            if text(timing, "status") == Some("not_evaluable") {
                message.push_str(
                    " 병용금기 규칙은 확인되지만 복용 간격 조건의 적용 여부를 추가로 확인해야 합니다.",
                );
            }
            let name = text(medication, "product_name").ok_or(())?;
            let mut finding = json!({
                "type": "combination_contraindication",
                "severity": "danger",
                "title": format!("{name}와 병용금기"),
                "details": message,
                "related_medication_id": medication.get("id").cloned().unwrap_or(Value::Null),
                "timing": timing_value,
                "source_scope": "canonical_product",
            });
            if !qualifier_values.is_empty() {
                finding["qualifiers"] = Value::Array(qualifier_values);
            }
            if text(timing, "status") == Some("not_evaluable") || qualifier_review {
                finding["evaluation_status"] = Value::String("conditional".to_owned());
            }
            risks.push(finding);
        }
        if rows.is_empty() && unlinked_combination_exists(con, target, paired)? {
            let name = text(medication, "product_name").ok_or(())?;
            risks.push(json!({
                "type": "combination_contraindication",
                "severity": "info",
                "title": format!("{name}와 병용금기 기준 확인 필요"),
                "details": "MFDS ITEM_SEQ 병용금기 규칙은 있으나 상세 기준 연결을 확정하지 못했습니다. 의사 또는 약사에게 확인하세요.",
                "related_medication_id": medication.get("id").cloned().unwrap_or(Value::Null),
                "evaluation_status": "unknown",
                "source_scope": "canonical_product",
            }));
        }
    }
    Ok(risks)
}

fn duplication_risks(
    con: &Connection,
    product: &Map<String, Value>,
    current: &[Map<String, Value>],
    candidate_course: &Map<String, Value>,
) -> Result<Vec<Value>, ()> {
    let new_groups = duplication_groups(con, product)?;
    let mut risks = Vec::new();
    for medication in current {
        if courses_overlap(candidate_course, medication) == Some(false) {
            continue;
        }
        let current_groups = duplication_groups(con, medication)?;
        for group in new_groups
            .keys()
            .filter(|group| current_groups.contains_key(*group))
        {
            let name = text(medication, "product_name").ok_or(())?;
            let details = format!("현재 복용 중인 {name}와 같은 효능군입니다.");
            let new_info = new_groups.get(group).ok_or(())?;
            let current_info = current_groups.get(group).ok_or(())?;
            let qualifier_values = dedupe_qualifiers(
                new_info
                    .qualifiers
                    .iter()
                    .chain(current_info.qualifiers.iter())
                    .cloned()
                    .collect(),
            );
            let conditional = new_info.requires_review || current_info.requires_review;
            let mut finding = json!({
                "type": "therapeutic_duplication_caution",
                "severity": "warning",
                "title": format!("효능군 중복주의 · {group}"),
                "details": if conditional {
                    details_with_professional_review(Some(&details))
                } else {
                    details
                },
                "related_medication_id": medication.get("id").cloned().unwrap_or(Value::Null),
                "source_scope": "canonical_product",
            });
            if !qualifier_values.is_empty() {
                finding["qualifiers"] = Value::Array(qualifier_values);
            }
            if conditional {
                finding["evaluation_status"] = Value::String("conditional".to_owned());
            }
            risks.push(finding);
        }
    }
    Ok(risks)
}

fn duplication_groups(
    con: &Connection,
    product: &Map<String, Value>,
) -> Result<BTreeMap<String, DuplicationGroup>, ()> {
    let Some(target) = item_seq(product) else {
        return Ok(BTreeMap::new());
    };
    let mut groups: BTreeMap<String, DuplicationGroup> = BTreeMap::new();
    for row in resolved_product_rows(con, target, "therapeutic_duplication_caution")? {
        let Some(group) = nonempty_text(&row, "criterion_rule_value")
            .or_else(|| nonempty_text(&row, "effect_name"))
        else {
            continue;
        };
        let semantics = semantic_records(con, &row)?;
        let exclusion = semantics
            .iter()
            .find(|semantic| text(semantic, "evaluator_kind") == Some("excluded_route"));
        let mut requires_review = criterion_note_requires_review(con, &row)?;
        if let Some(exclusion) = exclusion {
            let form = nonempty_text(&row, "product_dosage_form")
                .or_else(|| nonempty_text(product, "dosage_form"));
            let forms = form.map(|value| vec![value.to_owned()]).unwrap_or_default();
            let route = infer_administration_route(&forms);
            let excluded_route = exclusion
                .get("structured_payload")
                .and_then(Value::as_object)
                .and_then(|payload| payload.get("route"))
                .and_then(Value::as_str);
            if excluded_route == Some(route) {
                continue;
            }
            if route == "unknown" {
                requires_review = true;
            }
        }
        let entry = groups.entry(group.to_owned()).or_default();
        entry.requires_review |= requires_review;
        entry.qualifiers.extend(qualifiers(con, &row)?);
    }
    for entry in groups.values_mut() {
        entry.qualifiers = dedupe_qualifiers(std::mem::take(&mut entry.qualifiers));
    }
    Ok(groups)
}

fn dedupe_and_sort(values: Vec<Value>) -> Vec<Value> {
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for value in values {
        let key = (
            value.get("type").map(Value::to_string),
            value.get("title").map(Value::to_string),
            value.get("details").map(Value::to_string),
            value.get("related_medication_id").map(Value::to_string),
        );
        if seen.insert(key) {
            result.push(value);
        }
    }
    result.sort_by(|left, right| {
        severity_order(left)
            .cmp(&severity_order(right))
            .then_with(|| string_value(left, "title").cmp(string_value(right, "title")))
    });
    result
}

impl From<ProductError> for InteractionError {
    fn from(value: ProductError) -> Self {
        match value {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::Unavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

fn parse_object(raw: &str, name: &str) -> Result<Map<String, Value>, InteractionError> {
    let value = serde_json::from_str::<Value>(raw)
        .map_err(|_| InteractionError::BadRequest(format!("{name} must be valid JSON")))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| InteractionError::BadRequest(format!("{name} must be a JSON object")))
}

fn parse_object_array(raw: &str, name: &str) -> Result<Vec<Map<String, Value>>, InteractionError> {
    let value = serde_json::from_str::<Value>(raw)
        .map_err(|_| InteractionError::BadRequest(format!("{name} must be valid JSON")))?;
    let array = value
        .as_array()
        .ok_or_else(|| InteractionError::BadRequest(format!("{name} must be an array")))?;
    array
        .iter()
        .map(|value| {
            value.as_object().cloned().ok_or_else(|| {
                InteractionError::BadRequest(format!("{name} entries must be JSON objects"))
            })
        })
        .collect()
}

fn details_with_professional_review(details: Option<&str>) -> String {
    let advice = "세부 적용 조건이 있어 의사 또는 약사에게 확인하세요.";
    details
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map_or_else(|| advice.to_owned(), |value| format!("{value} {advice}"))
}

fn canonical_details(row: &Map<String, Value>) -> Option<String> {
    nonempty_text(row, "product_details")
        .or_else(|| nonempty_text(row, "criterion_details"))
        .map(str::to_owned)
}

fn item_seq(product: &Map<String, Value>) -> Option<&str> {
    ["catalog_item_seq", "product_ref", "product_code"]
        .iter()
        .find_map(|key| nonempty_text(product, key))
}

fn severity_order(value: &Value) -> u8 {
    match value.get("severity").and_then(Value::as_str) {
        Some("danger") => 0,
        Some("warning") => 1,
        Some("info") => 2,
        _ => 9,
    }
}

fn string_value<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or("")
}

fn nonempty_text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    text(row, key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}
