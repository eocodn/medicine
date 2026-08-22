use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;

use crate::assessment_runtime::{self, AssessmentError};
use crate::canonical_products::{self, ProductError};
use crate::personal_db::{self, Access, OpenError};
use crate::prescriptions::{self, DraftError};

const PREVIEW_FIELDS: [&str; 15] = [
    "product_ref",
    "product_code",
    "dosage_text",
    "dose_amount",
    "dose_unit",
    "frequency_per_day",
    "meal_relation",
    "administration_route",
    "as_needed",
    "prn_max_per_day",
    "prescription_days",
    "long_term",
    "schedule_times",
    "start_date",
    "end_date",
];

#[derive(Debug)]
enum PreviewError {
    BadRequest(String),
    NotFound(String),
    ReferenceUnavailable,
    PersonalUnavailable,
    Internal,
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    route(method, path).is_some()
}

pub(crate) fn handle_request(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    method: &str,
    path: &str,
    body_json: &str,
) -> Option<(u16, Value)> {
    let person_id = route(method, path)?;
    let result = build(canonical_db, personal_db, person_id, body_json);
    Some(match result {
        Ok(body) => (200, body),
        Err(PreviewError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(PreviewError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(PreviewError::ReferenceUnavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(PreviewError::PersonalUnavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(PreviewError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<&'a str> {
    if !method.trim().eq_ignore_ascii_case("POST") {
        return None;
    }
    let rest = path.strip_prefix("/api/people/")?;
    let person_id = rest.strip_suffix("/medications/preview")?;
    (!person_id.is_empty() && !person_id.contains('/')).then_some(person_id)
}

fn build(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    person_id: &str,
    body_json: &str,
) -> Result<Value, PreviewError> {
    let payload = validated_payload(body_json)?;
    let product_ref = product_reference(&payload)?;
    let mut draft_values = payload.clone();
    draft_values.remove("product_ref");
    draft_values.remove("product_code");
    let draft = prescriptions::normalize(&draft_values).map_err(PreviewError::from)?;

    let canonical = canonical_products::open(canonical_db).map_err(PreviewError::from)?;
    let mut product = canonical_products::resolve_from_connection(&canonical, product_ref)
        .map_err(PreviewError::from)?;
    product["med_source"] = Value::String("catalog_search".to_owned());

    let personal = personal_db::open(personal_db, Access::ReadOnly).map_err(PreviewError::from)?;
    let assessment =
        assessment_runtime::evaluate(&canonical, &personal, person_id, &product, &draft, false)
            .map_err(PreviewError::from)?;

    Ok(json!({
        "person": assessment.person,
        "product": product,
        "draft": draft,
        "current_medication_count": assessment.current_count,
        "risks": assessment.risks,
        "review_items": assessment.review_items,
        "dur_checks": assessment.dur_checks,
        "quantitative_checks": {"duration": assessment.duration, "dose": assessment.dose},
        "warning_token": assessment.warning_token,
        "coverage": assessment.coverage,
    }))
}

fn validated_payload(body_json: &str) -> Result<Map<String, Value>, PreviewError> {
    let value = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| PreviewError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let payload = value
        .as_object()
        .cloned()
        .ok_or_else(|| PreviewError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed = PREVIEW_FIELDS.into_iter().collect::<BTreeSet<_>>();
    let unknown = payload
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(PreviewError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(payload)
}

fn product_reference(payload: &Map<String, Value>) -> Result<&str, PreviewError> {
    for key in ["product_ref", "product_code"] {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            if !value.is_empty() {
                return Ok(value);
            }
        }
    }
    Err(PreviewError::BadRequest(
        "product_ref or product_code is required".to_owned(),
    ))
}

impl From<ProductError> for PreviewError {
    fn from(error: ProductError) -> Self {
        match error {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::ReferenceUnavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

impl From<DraftError> for PreviewError {
    fn from(error: DraftError) -> Self {
        match error {
            DraftError::BadRequest(detail) => Self::BadRequest(detail),
            DraftError::Internal => Self::Internal,
        }
    }
}

impl From<AssessmentError> for PreviewError {
    fn from(error: AssessmentError) -> Self {
        match error {
            AssessmentError::BadRequest(detail) => Self::BadRequest(detail),
            AssessmentError::NotFound(detail) => Self::NotFound(detail),
            AssessmentError::PersonalUnavailable => Self::PersonalUnavailable,
            AssessmentError::Internal => Self::Internal,
        }
    }
}

impl From<OpenError> for PreviewError {
    fn from(error: OpenError) -> Self {
        match error {
            OpenError::Unavailable => Self::PersonalUnavailable,
            OpenError::Sql => Self::Internal,
        }
    }
}
