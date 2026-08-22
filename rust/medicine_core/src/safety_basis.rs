use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::Path;

use crate::canonical_products::{self, ProductError};
use crate::prescriptions::{self, DraftError};
use crate::quantitative_safety;
use crate::reference_runtime;

enum BasisError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

pub(crate) fn inspect(
    canonical_db: Option<&Path>,
    product_ref: &str,
    person_json: &str,
    draft_json: &str,
) -> (u16, Value) {
    let result = build(canonical_db, product_ref, person_json, draft_json);
    match result {
        Ok(body) => (200, body),
        Err(BasisError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(BasisError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(BasisError::Unavailable) => (503, json!({"detail": "reference database unavailable"})),
        Err(BasisError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn build(
    canonical_db: Option<&Path>,
    product_ref: &str,
    person_json: &str,
    draft_json: &str,
) -> Result<Value, BasisError> {
    let person = parse_object(person_json, "person")?;
    let raw_draft = parse_object(draft_json, "draft")?;
    let draft = prescriptions::normalize(&raw_draft).map_err(BasisError::from)?;
    let con = canonical_products::open(canonical_db).map_err(BasisError::from)?;
    let product =
        canonical_products::resolve_from_connection(&con, product_ref).map_err(BasisError::from)?;
    let dataset = reference_runtime::manifest(&con).map_err(|_| BasisError::Internal)?;
    let item_seq = product
        .get("catalog_item_seq")
        .and_then(Value::as_str)
        .ok_or(BasisError::Internal)?;
    let issues = reference_runtime::category_resolution_issues(&con, item_seq)
        .map_err(|_| BasisError::Internal)?;
    let pregnancy_relevant =
        reference_runtime::has_product_category(&con, item_seq, "pregnancy_contraindication")
            .map_err(|_| BasisError::Internal)?;
    let coverage = coverage_summary(&product, &dataset, &person, &issues, pregnancy_relevant)?;
    let quantitative =
        quantitative_safety::evaluate(&con, &product, &draft).map_err(|_| BasisError::Internal)?;
    Ok(json!({
        "product": product,
        "draft": draft,
        "dataset": dataset,
        "coverage": coverage,
        "quantitative_checks": quantitative,
    }))
}

fn coverage_summary(
    product: &Value,
    dataset: &Value,
    person: &Map<String, Value>,
    issues: &BTreeMap<String, Vec<Value>>,
    pregnancy_relevant: bool,
) -> Result<Value, BasisError> {
    let product = product.as_object().ok_or(BasisError::Internal)?;
    let dataset_object = dataset.as_object().ok_or(BasisError::Internal)?;
    let product_status = product
        .get("product_mapping_status")
        .and_then(Value::as_str)
        .unwrap_or("not_matched");
    let reproductive_applicable = person.get("sex").and_then(Value::as_str) != Some("male");
    let pregnancy_unknown =
        person.get("pregnancy_status").and_then(Value::as_str) == Some("unknown");
    let profile_gaps = if reproductive_applicable && pregnancy_unknown && pregnancy_relevant {
        vec!["pregnancy_contraindication"]
    } else {
        Vec::new()
    };

    let mut not_evaluable = Vec::new();
    if dataset_object.get("status").and_then(Value::as_str) != Some("verified") {
        not_evaluable.push(json!({
            "category": "dataset",
            "result": "not_evaluable",
            "reason": "canonical DUR 데이터셋 검증 상태를 확인하지 못했습니다.",
        }));
    }
    if product_status != "matched" {
        not_evaluable.push(json!({
            "category": "product_mapping",
            "result": "not_evaluable",
            "reason": "MFDS ITEM_SEQ 제품을 canonical 데이터에 연결하지 못했습니다.",
        }));
    }
    for (category, rows) in issues {
        not_evaluable.push(json!({
            "category": category,
            "result": "not_evaluable",
            "reason": "MFDS ITEM_SEQ DUR 규칙은 있으나 상세 기준 연결을 확정하지 못했습니다. 의사 또는 약사에게 확인하세요.",
            "source_rows": rows,
        }));
    }
    for category in &profile_gaps {
        not_evaluable.push(json!({
            "category": category,
            "result": "not_evaluable",
            "reason": "임신 여부가 미확정이라 임부금기 적용 여부를 판정할 수 없습니다.",
        }));
    }
    let limited = !not_evaluable.is_empty();
    let category_resolution = issues
        .keys()
        .map(|category| (category.clone(), Value::String("unresolved".to_owned())))
        .collect::<Map<String, Value>>();
    Ok(json!({
        "status": if limited { "limited" } else { "complete" },
        "message": if limited {
            "일부 항목은 자동으로 확인하지 못했어요."
        } else {
            "현재 프로필과 canonical DUR 범위에서 확인했어요."
        },
        "dataset": dataset,
        "product": {
            "status": product_status,
            "identity_status": product.get("product_identity_status").and_then(Value::as_str).unwrap_or(product_status),
            "identity_method": product.get("product_identity_method").cloned().unwrap_or(Value::Null),
            "item_seq": product.get("catalog_item_seq").cloned().unwrap_or(Value::Null),
            "edi_codes": product.get("edi_codes").cloned().unwrap_or_else(|| json!([])),
        },
        "ingredient": {
            "status": "not_required",
            "mapping_method": "canonical_applicability",
        },
        "category_resolution": category_resolution,
        "profile": {"not_evaluable_categories": profile_gaps},
        "not_evaluable_checks": not_evaluable,
    }))
}

fn parse_object(raw: &str, name: &str) -> Result<Map<String, Value>, BasisError> {
    let value = serde_json::from_str::<Value>(raw)
        .map_err(|_| BasisError::BadRequest(format!("{name} must be valid JSON")))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| BasisError::BadRequest(format!("{name} must be a JSON object")))
}

impl From<ProductError> for BasisError {
    fn from(error: ProductError) -> Self {
        match error {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::Unavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

impl From<DraftError> for BasisError {
    fn from(error: DraftError) -> Self {
        match error {
            DraftError::BadRequest(detail) => Self::BadRequest(detail),
            DraftError::Internal => Self::Internal,
        }
    }
}
