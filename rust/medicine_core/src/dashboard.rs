use rusqlite::{Connection, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::path::Path;

use crate::assessment_runtime::{self, AssessmentError, AssessmentScope};
use crate::canonical_products::{self, ProductError};
use crate::dose_logs;
use crate::medication_records::RecordError;
use crate::people::{self, PeopleError};
use crate::personal_db::{self, Access, OpenError};
use crate::planning::{self, PlanningError};
use crate::planning_medications::load_active_medications;

#[derive(Debug)]
pub(crate) enum DashboardError {
    BadRequest(String),
    NotFound(String),
    ReferenceUnavailable,
    Unavailable,
    Internal,
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    method.trim().eq_ignore_ascii_case("GET")
        && path
            .strip_prefix("/api/people/")
            .and_then(|rest| rest.strip_suffix("/dashboard"))
            .is_some_and(|person_id| !person_id.is_empty() && !person_id.contains('/'))
}

pub(crate) fn handle_request(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    method: &str,
    raw_path: &str,
    path: &str,
) -> Option<(u16, Value)> {
    if !handles_request(method, path) {
        return None;
    }
    Some(
        match compose(canonical_db, personal_db, path_id(path)?, raw_path) {
            Ok(body) => (200, body),
            Err(DashboardError::BadRequest(detail)) => (400, json!({"detail": detail})),
            Err(DashboardError::NotFound(detail)) => (404, json!({"detail": detail})),
            Err(DashboardError::ReferenceUnavailable) => {
                (503, json!({"detail": "reference database unavailable"}))
            }
            Err(DashboardError::Unavailable) => {
                (503, json!({"detail": "personal database unavailable"}))
            }
            Err(DashboardError::Internal) => (500, json!({"detail": "unexpected server error"})),
        },
    )
}

fn path_id(path: &str) -> Option<&str> {
    path.strip_prefix("/api/people/")?
        .strip_suffix("/dashboard")
}

pub(crate) fn compose(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    person_id: &str,
    raw_path: &str,
) -> Result<Value, DashboardError> {
    let target = planning::target_date(raw_path).map_err(DashboardError::from)?;
    let mut personal = personal_db::open(personal_db, Access::ReadWrite).map_err(map_open)?;
    let transaction = personal
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| DashboardError::Internal)?;
    let person = people::load_person(&transaction, person_id).map_err(DashboardError::from)?;
    let mut medications =
        load_active_medications(&transaction, person_id, target).map_err(DashboardError::from)?;

    // Reference data is optional for the dashboard. When unavailable, the
    // medication and planning payload remain useful but no stale assessment is
    // presented as current.
    let canonical = canonical_db
        .map(|path| canonical_products::open(Some(path)))
        .transpose()
        .map_err(DashboardError::from)?;
    if let Some(canonical) = canonical.as_ref() {
        for medication in &mut medications {
            let medication_json = medication.to_json();
            let id = medication.id().map_err(DashboardError::from)?.to_owned();
            let product = current_product(canonical, &medication_json);
            let draft = current_draft(&medication_json)?;
            let bundle = assessment_runtime::evaluate_scoped(
                canonical,
                &transaction,
                person_id,
                &product,
                &draft,
                false,
                AssessmentScope {
                    exclude_medication_id: Some(&id),
                    as_of: Some(target),
                    bind_confirmation_token: false,
                },
            )
            .map_err(DashboardError::from)?;
            let mut assessment = bundle.assessment;
            add_permit_fields(&mut assessment, &product);
            let dur_alert = has_dur_alert(&assessment);
            let split_prohibited = has_split_prohibition(&assessment);
            let dur_review_required = has_unknown_dur(&assessment);
            let review_required = assessment
                .get("requires_review")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            medication.insert("current_assessment", Value::Object(assessment));
            medication.insert(
                "permit_status",
                product.get("permit_status").cloned().unwrap_or(Value::Null),
            );
            medication.insert(
                "permit_status_name",
                product
                    .get("permit_status_name")
                    .cloned()
                    .unwrap_or(Value::Null),
            );
            medication.insert(
                "permit_status_changed_at",
                product.get("cancel_date").cloned().unwrap_or(Value::Null),
            );
            medication.insert("dur_alert", Value::Bool(dur_alert));
            medication.insert("split_prohibited", Value::Bool(split_prohibited));
            medication.insert("dur_review_required", Value::Bool(dur_review_required));
            medication.insert("review_required", Value::Bool(review_required));
        }
    }

    let daily_plan =
        planning::materialize_for_dashboard(&transaction, person_id, &medications, target)
            .map_err(DashboardError::from)?;
    let recent_logs = dose_logs::recent_logs(&transaction, person_id, 20)
        .map_err(|_| DashboardError::Internal)?;
    transaction.commit().map_err(|_| DashboardError::Internal)?;
    Ok(json!({
        "person": person,
        "medications": medications.into_iter().map(|medication| medication.to_json()).collect::<Vec<_>>(),
        "recent_logs": recent_logs,
        "daily_plan": daily_plan,
    }))
}

fn current_product(canonical: &Connection, medication: &Value) -> Value {
    let reference = medication
        .get("catalog_item_seq")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty());
    if let Some(reference) = reference {
        if let Ok(product) = canonical_products::resolve_from_connection(canonical, reference) {
            return merge_product(medication, product);
        }
    }
    fallback_product(medication)
}

fn merge_product(medication: &Value, product: Value) -> Value {
    let mut result = medication.as_object().cloned().unwrap_or_default();
    if let Value::Object(product) = product {
        result.extend(product);
    }
    if let Some(id) = medication.get("id").cloned() {
        result.insert("id".to_owned(), id);
    }
    Value::Object(result)
}

fn fallback_product(medication: &Value) -> Value {
    let mut result = medication.as_object().cloned().unwrap_or_default();
    result.extend([
        ("safety_ingredients".to_owned(), json!([])),
        (
            "ingredient_mapping_status".to_owned(),
            json!("not_required"),
        ),
        (
            "ingredient_mapping_method".to_owned(),
            json!("canonical_applicability"),
        ),
        ("product_mapping_status".to_owned(), json!("not_matched")),
        ("product_identity_status".to_owned(), json!("not_matched")),
        ("product_identity_method".to_owned(), Value::Null),
        ("matched_product_codes".to_owned(), json!([])),
        ("edi_codes".to_owned(), json!([])),
        ("canonical_resolution_issues".to_owned(), json!({})),
    ]);
    Value::Object(result)
}

fn current_draft(medication: &Value) -> Result<Value, DashboardError> {
    let object = medication.as_object().ok_or(DashboardError::Internal)?;
    let schedule_times = object
        .get("schedules")
        .and_then(Value::as_array)
        .ok_or(DashboardError::Internal)?
        .iter()
        .map(|schedule| {
            schedule
                .get("time_of_day")
                .and_then(Value::as_str)
                .map(str::to_owned)
                .ok_or(DashboardError::Internal)
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "dosage_text": object.get("dosage_text").cloned().unwrap_or(Value::Null),
        "dose_amount": object.get("dose_amount").cloned().unwrap_or(Value::Null),
        "dose_unit": object.get("dose_unit").cloned().unwrap_or(Value::Null),
        "frequency_per_day": object.get("frequency_per_day").cloned().unwrap_or(Value::Null),
        "meal_relation": object.get("meal_relation").and_then(Value::as_str).unwrap_or("unspecified"),
        "administration_route": object.get("administration_route").and_then(Value::as_str).unwrap_or("unknown"),
        "as_needed": object.get("as_needed").and_then(Value::as_bool).unwrap_or(false),
        "prn_max_per_day": object.get("prn_max_per_day").cloned().unwrap_or(Value::Null),
        "prescription_days": object.get("prescription_days").cloned().unwrap_or(Value::Null),
        "long_term": object.get("long_term").and_then(Value::as_bool).unwrap_or(false),
        "schedule_times": schedule_times,
        "start_date": object.get("start_date").cloned().unwrap_or(Value::Null),
        "end_date": object.get("end_date").cloned().unwrap_or(Value::Null),
    }))
}

fn add_permit_fields(assessment: &mut Map<String, Value>, product: &Value) {
    assessment.insert(
        "permit_status".to_owned(),
        product.get("permit_status").cloned().unwrap_or(Value::Null),
    );
    assessment.insert(
        "permit_status_name".to_owned(),
        product
            .get("permit_status_name")
            .cloned()
            .unwrap_or(Value::Null),
    );
    assessment.insert(
        "permit_status_changed_at".to_owned(),
        product.get("cancel_date").cloned().unwrap_or(Value::Null),
    );
}

fn has_dur_alert(assessment: &Map<String, Value>) -> bool {
    if let Some(items) = assessment.get("dur_checks").and_then(Value::as_array) {
        return items.iter().any(|item| {
            matches!(
                item.get("status").and_then(Value::as_str),
                Some("hit" | "conditional")
            ) && item.get("category").and_then(Value::as_str) != Some("split_caution")
        });
    }
    assessment
        .get("risks")
        .and_then(Value::as_array)
        .is_some_and(|risks| {
            risks.iter().any(|risk| {
                matches!(
                    risk.get("severity").and_then(Value::as_str),
                    Some("danger" | "warning")
                ) && risk.get("evaluation_status").and_then(Value::as_str) != Some("unknown")
            })
        })
        || ["duration", "dose"].iter().any(|name| {
            assessment
                .get(*name)
                .and_then(|value| value.get("result"))
                .and_then(Value::as_str)
                == Some("exceeded")
        })
}

fn has_split_prohibition(assessment: &Map<String, Value>) -> bool {
    assessment
        .get("dur_checks")
        .and_then(Value::as_array)
        .is_some_and(|items| {
            items.iter().any(|item| {
                item.get("category").and_then(Value::as_str) == Some("split_caution")
                    && item.get("status").and_then(Value::as_str) == Some("hit")
                    && item
                        .get("summary")
                        .and_then(Value::as_str)
                        .map(|summary| summary.split_whitespace().collect::<String>() == "분할불가")
                        .unwrap_or(false)
            })
        })
}

fn has_unknown_dur(assessment: &Map<String, Value>) -> bool {
    assessment
        .get("dur_checks")
        .and_then(Value::as_array)
        .is_some_and(|items| {
            items
                .iter()
                .any(|item| item.get("status").and_then(Value::as_str) == Some("unknown"))
        })
}

fn map_open(error: OpenError) -> DashboardError {
    match error {
        OpenError::Unavailable => DashboardError::Unavailable,
        OpenError::Sql => DashboardError::Internal,
    }
}

impl From<PlanningError> for DashboardError {
    fn from(error: PlanningError) -> Self {
        match error {
            PlanningError::BadRequest(detail) => Self::BadRequest(detail),
            PlanningError::NotFound(detail) => Self::NotFound(detail),
            PlanningError::Unavailable => Self::Unavailable,
            PlanningError::Internal => Self::Internal,
        }
    }
}

impl From<PeopleError> for DashboardError {
    fn from(error: PeopleError) -> Self {
        match error {
            PeopleError::BadRequest(detail) => Self::BadRequest(detail),
            PeopleError::NotFound(detail) => Self::NotFound(detail),
            PeopleError::Unavailable => Self::Unavailable,
            PeopleError::Internal => Self::Internal,
        }
    }
}

impl From<RecordError> for DashboardError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
        }
    }
}

impl From<AssessmentError> for DashboardError {
    fn from(error: AssessmentError) -> Self {
        match error {
            AssessmentError::BadRequest(detail) => Self::BadRequest(detail),
            AssessmentError::NotFound(detail) => Self::NotFound(detail),
            AssessmentError::PersonalUnavailable => Self::Unavailable,
            AssessmentError::Internal => Self::Internal,
        }
    }
}

impl From<ProductError> for DashboardError {
    fn from(error: ProductError) -> Self {
        match error {
            ProductError::Unavailable => Self::ReferenceUnavailable,
            ProductError::BadRequest(detail) | ProductError::NotFound(detail) => {
                Self::BadRequest(detail)
            }
            ProductError::Internal => Self::Internal,
        }
    }
}
