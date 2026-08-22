use rusqlite::TransactionBehavior;
use serde_json::{json, Value};
use std::path::Path;

use crate::dose_logs;
use crate::medication_list::{self, MedicationListError};
use crate::people::{self, PeopleError};
use crate::personal_db::{self, Access, OpenError};
use crate::planning::{self, PlanningError};

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
    let medications = medication_list::compose(canonical_db, &transaction, person_id, target)
        .map_err(DashboardError::from)?;

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

impl From<MedicationListError> for DashboardError {
    fn from(error: MedicationListError) -> Self {
        match error {
            MedicationListError::BadRequest(detail) => Self::BadRequest(detail),
            MedicationListError::NotFound(detail) => Self::NotFound(detail),
            MedicationListError::ReferenceUnavailable => Self::ReferenceUnavailable,
            MedicationListError::Unavailable => Self::Unavailable,
            MedicationListError::Internal => Self::Internal,
        }
    }
}
