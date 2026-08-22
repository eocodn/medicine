use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub(crate) const EVALUATOR_VERSION: &str = "13-mfds-reviewed-fallbacks-no-lactation";

pub(crate) fn bind(
    assessment: &mut Map<String, Value>,
    payload_hash: &str,
) -> Result<Option<String>, ()> {
    assessment.insert(
        "draft_fingerprint".to_owned(),
        Value::String(payload_hash.to_owned()),
    );
    let requires_review = assessment
        .get("requires_review")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if !requires_review {
        assessment.insert("warning_token".to_owned(), Value::Null);
        return Ok(None);
    }

    let dataset_id = assessment
        .get("dataset")
        .and_then(Value::as_object)
        .and_then(|dataset| dataset.get("dataset_id"))
        .and_then(Value::as_str)
        .unwrap_or("dataset:unverified");
    let coverage = warning_coverage(assessment.get("coverage"))?;
    let mut context = Map::new();
    context.insert("coverage".to_owned(), coverage);
    context.insert(
        "risks".to_owned(),
        assessment.get("risks").cloned().unwrap_or(Value::Null),
    );
    context.insert(
        "review_items".to_owned(),
        assessment
            .get("review_items")
            .cloned()
            .unwrap_or(Value::Null),
    );
    context.insert(
        "duration".to_owned(),
        assessment.get("duration").cloned().unwrap_or(Value::Null),
    );
    context.insert(
        "dose".to_owned(),
        assessment.get("dose").cloned().unwrap_or(Value::Null),
    );
    context.insert(
        "dur_checks".to_owned(),
        assessment.get("dur_checks").cloned().unwrap_or(Value::Null),
    );
    context.insert("requires_review".to_owned(), Value::Bool(requires_review));

    let encoded = serde_json::to_string(&Value::Object(context)).map_err(|_| ())?;
    let context_hash = format!("{:x}", Sha256::digest(encoded.as_bytes()));
    let token_source = format!("{EVALUATOR_VERSION}\0{dataset_id}\0{payload_hash}\0{context_hash}");
    let token = format!("{:x}", Sha256::digest(token_source.as_bytes()));
    assessment.insert("warning_token".to_owned(), Value::String(token.clone()));
    Ok(Some(token))
}

fn warning_coverage(value: Option<&Value>) -> Result<Value, ()> {
    let Some(Value::Object(source)) = value else {
        return Ok(value.cloned().unwrap_or(Value::Null));
    };
    let mut coverage = source.clone();
    if let Some(Value::Object(dataset)) = coverage.get("dataset") {
        let mut logical = Map::new();
        for key in ["status", "dataset_id", "contract_major"] {
            if let Some(value) = dataset.get(key) {
                logical.insert(key.to_owned(), value.clone());
            }
        }
        coverage.insert("dataset".to_owned(), Value::Object(logical));
    }
    Ok(Value::Object(coverage))
}
