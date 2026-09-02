use chrono::NaiveDate;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

use crate::assessment_token::{self, EVALUATOR_VERSION};
use crate::current_products;
use crate::dur_display;
use crate::interaction_safety;
use crate::medication_records::RecordError;
use crate::people::{self, PeopleError};
use crate::prescriptions::{self, DraftError};
use crate::profile_safety;
use crate::quantitative_safety;
use crate::reference_queries;
use crate::regimen_review;
use crate::safety_basis;
use crate::safety_time::age_years;

#[derive(Debug)]
pub(crate) enum AssessmentError {
    BadRequest(String),
    NotFound(String),
    PersonalUnavailable,
    Internal,
}

pub(crate) struct AssessmentBundle {
    pub(crate) person: Value,
    pub(crate) current_count: usize,
    pub(crate) risks: Vec<Value>,
    pub(crate) review_items: Vec<Value>,
    pub(crate) dur_checks: Value,
    pub(crate) duration: Value,
    pub(crate) dose: Value,
    pub(crate) coverage: Value,
    pub(crate) assessment: Map<String, Value>,
    pub(crate) warning_token: Option<String>,
    pub(crate) payload_hash: String,
}

pub(crate) struct AssessmentScope<'a> {
    pub(crate) exclude_medication_id: Option<&'a str>,
    pub(crate) as_of: Option<NaiveDate>,
    pub(crate) bind_confirmation_token: bool,
}

pub(crate) fn evaluate(
    canonical: &Connection,
    personal: &Connection,
    person_id: &str,
    product: &Value,
    draft: &Value,
    acknowledged: bool,
) -> Result<AssessmentBundle, AssessmentError> {
    evaluate_excluding_medication(
        canonical,
        personal,
        person_id,
        product,
        draft,
        acknowledged,
        None,
    )
}

pub(crate) fn evaluate_excluding_medication(
    canonical: &Connection,
    personal: &Connection,
    person_id: &str,
    product: &Value,
    draft: &Value,
    acknowledged: bool,
    exclude_medication_id: Option<&str>,
) -> Result<AssessmentBundle, AssessmentError> {
    evaluate_scoped(
        canonical,
        personal,
        person_id,
        product,
        draft,
        acknowledged,
        AssessmentScope {
            exclude_medication_id,
            as_of: None,
            bind_confirmation_token: true,
        },
    )
}

pub(crate) fn evaluate_scoped(
    canonical: &Connection,
    personal: &Connection,
    person_id: &str,
    product: &Value,
    draft: &Value,
    acknowledged: bool,
    scope: AssessmentScope<'_>,
) -> Result<AssessmentBundle, AssessmentError> {
    let product_object = product.as_object().ok_or(AssessmentError::Internal)?;
    let draft_object = draft.as_object().ok_or(AssessmentError::Internal)?;
    let person = people::load_person(personal, person_id).map_err(AssessmentError::from)?;
    let person_object = person.as_object().ok_or(AssessmentError::Internal)?;
    let (current, current_count) = current_products::load_for_preview_excluding(
        personal,
        canonical,
        person_id,
        scope.exclude_medication_id,
    )
    .map_err(AssessmentError::from)?;

    let review_items = regimen_review::duplicate_review_items(&current, product, draft_object)
        .map_err(|_| AssessmentError::Internal)?;
    let (first_profile_date, last_profile_date) = profile_dates(draft_object, scope.as_of)?;

    let target_item_seq = item_seq(product_object);
    let mut risks = if target_item_seq.is_some() {
        profile_safety::evaluate(
            canonical,
            product,
            person_object,
            Some(first_profile_date),
            draft_object,
        )
        .map_err(|_| AssessmentError::Internal)?
    } else {
        Vec::new()
    };
    risks.extend(
        interaction_safety::evaluate(canonical, product, &current, draft_object)
            .map_err(|_| AssessmentError::Internal)?,
    );
    let risks = dedupe_and_sort_risks(risks);

    let dataset = reference_queries::manifest(canonical).map_err(|_| AssessmentError::Internal)?;
    let issues = match target_item_seq {
        Some(item_seq) => reference_queries::category_resolution_issues(canonical, item_seq)
            .map_err(|_| AssessmentError::Internal)?,
        None => BTreeMap::new(),
    };
    let pregnancy_relevant = match target_item_seq {
        Some(item_seq) => reference_queries::has_product_category(
            canonical,
            item_seq,
            "pregnancy_contraindication",
        )
        .map_err(|_| AssessmentError::Internal)?,
        None => false,
    };
    let coverage = safety_basis::coverage_for_product(
        product,
        &dataset,
        person_object,
        &issues,
        pregnancy_relevant,
    )
    .map_err(|_| AssessmentError::Internal)?;

    let mut quantitative = quantitative_safety::evaluate(canonical, product, draft)
        .map_err(|_| AssessmentError::Internal)?;
    apply_pediatric_review(person_object, first_profile_date, &mut quantitative)?;
    let duration = quantitative
        .get("duration")
        .cloned()
        .ok_or(AssessmentError::Internal)?;
    let dose = quantitative
        .get("dose")
        .cloned()
        .ok_or(AssessmentError::Internal)?;

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
        return Err(AssessmentError::Internal);
    }
    let dur_checks = display
        .get("dur_checks")
        .cloned()
        .ok_or(AssessmentError::Internal)?;
    let requires_review = display
        .get("requires_review")
        .and_then(Value::as_bool)
        .ok_or(AssessmentError::Internal)?;

    let product_ref = item_seq(product_object);
    let payload_hash = prescriptions::draft_hash_optional(person_id, product_ref, draft)
        .map_err(AssessmentError::from)?;
    let mut assessment = Map::new();
    assessment.insert(
        "evaluator_version".to_owned(),
        Value::String(EVALUATOR_VERSION.to_owned()),
    );
    assessment.insert("dataset".to_owned(), dataset);
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
    assessment.insert("acknowledged".to_owned(), Value::Bool(acknowledged));
    let warning_token = if scope.bind_confirmation_token {
        assessment_token::bind(&mut assessment, &payload_hash)
            .map_err(|_| AssessmentError::Internal)?
    } else {
        None
    };

    Ok(AssessmentBundle {
        person,
        current_count,
        risks,
        review_items,
        dur_checks,
        duration,
        dose,
        coverage,
        assessment,
        warning_token,
        payload_hash,
    })
}

fn profile_dates(
    draft: &Map<String, Value>,
    as_of: Option<NaiveDate>,
) -> Result<(NaiveDate, NaiveDate), AssessmentError> {
    let start = draft
        .get("start_date")
        .and_then(Value::as_str)
        .map(parse_date)
        .transpose()?;
    let end = match draft.get("end_date").and_then(Value::as_str) {
        Some(value) => Some(parse_date(value)?),
        None => None,
    };
    let first = match (start, as_of) {
        (Some(start), Some(target)) => start.max(target),
        (Some(start), None) => start,
        (None, Some(target)) => target,
        (None, None) => return Err(AssessmentError::Internal),
    };
    let last = end.filter(|end| *end >= first).unwrap_or(first);
    Ok((first, last))
}

fn apply_pediatric_review(
    person: &Map<String, Value>,
    as_of: NaiveDate,
    quantitative: &mut Value,
) -> Result<(), AssessmentError> {
    let birth_date = person
        .get("birth_date")
        .and_then(Value::as_str)
        .ok_or(AssessmentError::Internal)?;
    if age_years(birth_date, Some(as_of)).map_err(|_| AssessmentError::Internal)? >= 19 {
        return Ok(());
    }
    let dose = quantitative
        .get_mut("dose")
        .and_then(Value::as_object_mut)
        .ok_or(AssessmentError::Internal)?;
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
        .ok_or(AssessmentError::Internal)?;
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

fn item_seq(product: &Map<String, Value>) -> Option<&str> {
    ["catalog_item_seq", "product_ref", "product_code"]
        .iter()
        .find_map(|key| {
            product
                .get(*key)
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
        })
}

fn parse_date(value: &str) -> Result<NaiveDate, AssessmentError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| AssessmentError::Internal)
}

impl From<PeopleError> for AssessmentError {
    fn from(error: PeopleError) -> Self {
        match error {
            PeopleError::BadRequest(detail) => Self::BadRequest(detail),
            PeopleError::NotFound(detail) => Self::NotFound(detail),
            PeopleError::Unavailable => Self::PersonalUnavailable,
            PeopleError::Internal => Self::Internal,
        }
    }
}

impl From<RecordError> for AssessmentError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
        }
    }
}

impl From<DraftError> for AssessmentError {
    fn from(error: DraftError) -> Self {
        match error {
            DraftError::BadRequest(detail) => Self::BadRequest(detail),
            DraftError::Internal => Self::Internal,
        }
    }
}
