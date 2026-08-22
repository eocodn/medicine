use chrono::NaiveDate;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;

use crate::assessment_token::{self, EVALUATOR_VERSION};
use crate::canonical_products::{self, ProductError};
use crate::current_products;
use crate::dur_display;
use crate::interaction_safety;
use crate::medication_records::RecordError;
use crate::people::{self, PeopleError};
use crate::personal_db::{self, Access, OpenError};
use crate::prescriptions::{self, DraftError};
use crate::profile_safety;
use crate::quantitative_safety;
use crate::reference_runtime;
use crate::regimen_review;
use crate::safety_basis;
use crate::safety_time::age_years;

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
    let draft_object = draft.as_object().ok_or(PreviewError::Internal)?;

    let canonical = canonical_products::open(canonical_db).map_err(PreviewError::from)?;
    let mut product = canonical_products::resolve_from_connection(&canonical, product_ref)
        .map_err(PreviewError::from)?;
    product["med_source"] = Value::String("catalog_search".to_owned());

    let personal = personal_db::open(personal_db, Access::ReadOnly).map_err(PreviewError::from)?;
    let person = people::load_person(&personal, person_id).map_err(PreviewError::from)?;
    let person_object = person.as_object().ok_or(PreviewError::Internal)?;
    let (current, current_count) =
        current_products::load_for_preview(&personal, &canonical, person_id)
            .map_err(PreviewError::from)?;

    let review_items = regimen_review::duplicate_review_items(&current, &product, draft_object)
        .map_err(|_| PreviewError::Internal)?;
    let (first_profile_date, last_profile_date) = profile_dates(draft_object)?;

    let mut risks = profile_safety::evaluate(
        &canonical,
        &product,
        person_object,
        Some(first_profile_date),
        draft_object,
    )
    .map_err(|_| PreviewError::Internal)?;
    risks.extend(
        interaction_safety::evaluate(&canonical, &product, &current, draft_object)
            .map_err(|_| PreviewError::Internal)?,
    );
    let risks = dedupe_and_sort_risks(risks);

    let dataset = reference_runtime::manifest(&canonical).map_err(|_| PreviewError::Internal)?;
    let item_seq = product
        .get("catalog_item_seq")
        .and_then(Value::as_str)
        .ok_or(PreviewError::Internal)?;
    let issues = reference_runtime::category_resolution_issues(&canonical, item_seq)
        .map_err(|_| PreviewError::Internal)?;
    let pregnancy_relevant =
        reference_runtime::has_product_category(&canonical, item_seq, "pregnancy_contraindication")
            .map_err(|_| PreviewError::Internal)?;
    let coverage = safety_basis::coverage_for_product(
        &product,
        &dataset,
        person_object,
        &issues,
        pregnancy_relevant,
    )
    .map_err(|_| PreviewError::Internal)?;

    let mut quantitative = quantitative_safety::evaluate(&canonical, &product, &draft)
        .map_err(|_| PreviewError::Internal)?;
    apply_pediatric_review(person_object, first_profile_date, &mut quantitative)?;
    let duration = quantitative
        .get("duration")
        .cloned()
        .ok_or(PreviewError::Internal)?;
    let dose = quantitative
        .get("dose")
        .cloned()
        .ok_or(PreviewError::Internal)?;

    let detailed_categories = product
        .get("canonical_linked_categories")
        .cloned()
        .unwrap_or_else(|| json!([]));
    let display_input = json!({
        "person": person,
        "current": current,
        "risks": risks,
        "duration": duration,
        "dose": dose,
        "coverage": coverage,
        "dataset": dataset,
        "candidate_course": draft,
        "product": product,
        "detailed_product_categories": detailed_categories,
        "review_items": review_items,
        "as_of": first_profile_date.format("%Y-%m-%d").to_string(),
        "fallback_as_of": last_profile_date.format("%Y-%m-%d").to_string(),
    });
    let (display_status, display) = dur_display::inspect(&display_input.to_string());
    if display_status != 200 {
        return Err(PreviewError::Internal);
    }
    let dur_checks = display
        .get("dur_checks")
        .cloned()
        .ok_or(PreviewError::Internal)?;
    let requires_review = display
        .get("requires_review")
        .and_then(Value::as_bool)
        .ok_or(PreviewError::Internal)?;

    let product_ref = product
        .get("product_ref")
        .and_then(Value::as_str)
        .ok_or(PreviewError::Internal)?;
    let payload_hash =
        prescriptions::draft_hash(person_id, product_ref, &draft).map_err(PreviewError::from)?;
    let mut assessment = Map::new();
    assessment.insert(
        "evaluator_version".to_owned(),
        Value::String(EVALUATOR_VERSION.to_owned()),
    );
    assessment.insert("dataset".to_owned(), dataset.clone());
    assessment.insert("coverage".to_owned(), coverage.clone());
    assessment.insert("risks".to_owned(), Value::Array(risks.clone()));
    assessment.insert(
        "review_items".to_owned(),
        Value::Array(review_items.clone()),
    );
    assessment.insert("dur_checks".to_owned(), dur_checks.clone());
    assessment.insert("duration".to_owned(), duration.clone());
    assessment.insert("dose".to_owned(), dose.clone());
    assessment.insert("requires_review".to_owned(), Value::Bool(requires_review));
    assessment.insert("acknowledged".to_owned(), Value::Bool(false));
    let warning_token = assessment_token::bind(&mut assessment, &payload_hash)
        .map_err(|_| PreviewError::Internal)?;

    Ok(json!({
        "person": person,
        "product": product,
        "draft": draft,
        "current_medication_count": current_count,
        "risks": risks,
        "review_items": review_items,
        "dur_checks": dur_checks,
        "quantitative_checks": {"duration": duration, "dose": dose},
        "warning_token": warning_token,
        "coverage": coverage,
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

fn profile_dates(draft: &Map<String, Value>) -> Result<(NaiveDate, NaiveDate), PreviewError> {
    let start = draft
        .get("start_date")
        .and_then(Value::as_str)
        .ok_or(PreviewError::Internal)
        .and_then(parse_date)?;
    let end = match draft.get("end_date").and_then(Value::as_str) {
        Some(value) => parse_date(value)?,
        None => start,
    };
    Ok((start, end.max(start)))
}

fn apply_pediatric_review(
    person: &Map<String, Value>,
    as_of: NaiveDate,
    quantitative: &mut Value,
) -> Result<(), PreviewError> {
    let birth_date = person
        .get("birth_date")
        .and_then(Value::as_str)
        .ok_or(PreviewError::Internal)?;
    if age_years(birth_date, Some(as_of)).map_err(|_| PreviewError::Internal)? >= 19 {
        return Ok(());
    }
    let dose = quantitative
        .get_mut("dose")
        .and_then(Value::as_object_mut)
        .ok_or(PreviewError::Internal)?;
    if matches!(
        dose.get("result").and_then(Value::as_str),
        Some("within" | "not_applicable")
    ) {
        *dose = json!({
            "result": "not_evaluable",
            "reason": "adult dose-caution threshold is not a pediatric dose criterion",
            "source_scope": "profile",
            "pediatric_review": true,
        })
        .as_object()
        .cloned()
        .ok_or(PreviewError::Internal)?;
    } else {
        dose.insert("pediatric_review".to_owned(), Value::Bool(true));
    }
    Ok(())
}

fn dedupe_and_sort_risks(risks: Vec<Value>) -> Vec<Value> {
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for risk in risks {
        let key = (
            risk.get("type").map(Value::to_string),
            risk.get("title").map(Value::to_string),
            risk.get("details").map(Value::to_string),
            risk.get("related_medication_id").map(Value::to_string),
        );
        if seen.insert(key) {
            result.push(risk);
        }
    }
    result.sort_by_key(|risk| {
        let severity = match risk.get("severity").and_then(Value::as_str) {
            Some("danger") => 0,
            Some("warning") => 1,
            Some("info") => 2,
            _ => 9,
        };
        let title = risk
            .get("title")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        (severity, title)
    });
    result
}

fn parse_date(value: &str) -> Result<NaiveDate, PreviewError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| PreviewError::Internal)
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

impl From<PeopleError> for PreviewError {
    fn from(error: PeopleError) -> Self {
        match error {
            PeopleError::BadRequest(detail) => Self::BadRequest(detail),
            PeopleError::NotFound(detail) => Self::NotFound(detail),
            PeopleError::Unavailable => Self::PersonalUnavailable,
            PeopleError::Internal => Self::Internal,
        }
    }
}

impl From<RecordError> for PreviewError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
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

impl From<OpenError> for PreviewError {
    fn from(error: OpenError) -> Self {
        match error {
            OpenError::Unavailable => Self::PersonalUnavailable,
            OpenError::Sql => Self::Internal,
        }
    }
}
