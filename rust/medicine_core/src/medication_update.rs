use chrono::{FixedOffset, Utc};
use rusqlite::types::Value as SqlValue;
use rusqlite::{params_from_iter, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;
use uuid::Uuid;

use crate::assessment_runtime::{self, AssessmentError};
use crate::canonical_products::{self, ProductError};
use crate::medication_records::{self, RecordError};
use crate::personal_db::{self, Access, OpenError};
use crate::prescriptions::{self, DraftError};

const UPDATE_FIELDS: [&str; 16] = [
    "expected_revision",
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
    "acknowledge_warnings",
    "warning_token",
];

#[derive(Debug)]
enum UpdateError {
    BadRequest(String),
    Conflict(String),
    Confirmation { assessment: Map<String, Value> },
    NotFound(String),
    ReferenceUnavailable,
    PersonalUnavailable,
    Internal,
}

pub(crate) fn handle(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    medication_id: &str,
    body_json: &str,
) -> (u16, Value) {
    match update(canonical_db, personal_db, medication_id, body_json) {
        Ok(body) => (200, body),
        Err(UpdateError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(UpdateError::Conflict(detail)) => (409, json!({"detail": detail})),
        Err(UpdateError::Confirmation { assessment }) => (
            409,
            json!({
                "confirmation_required": true,
                "request_id": null,
                "warning_token": assessment.get("warning_token").cloned().unwrap_or(Value::Null),
                "assessment": assessment,
            }),
        ),
        Err(UpdateError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(UpdateError::ReferenceUnavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(UpdateError::PersonalUnavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(UpdateError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn update(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    medication_id: &str,
    body_json: &str,
) -> Result<Value, UpdateError> {
    let payload = validated_payload(body_json)?;
    if !payload.contains_key("expected_revision") {
        return Err(UpdateError::BadRequest(
            "expected_revision is required".to_owned(),
        ));
    }
    let expected_revision = payload
        .get("expected_revision")
        .and_then(Value::as_i64)
        .ok_or_else(|| {
            UpdateError::BadRequest("expected_revision must be an integer".to_owned())
        })?;
    let acknowledge = payload
        .get("acknowledge_warnings")
        .is_some_and(python_truthy);
    let warning_token = payload.get("warning_token").and_then(Value::as_str);

    // Product identity is authoritative from the stored medication. Existing
    // medications may retain an inactive canonical permit; update must not
    // apply the create-only active-permit gate.
    let mut personal =
        personal_db::open(personal_db, Access::ReadWrite).map_err(UpdateError::from)?;
    let transaction = personal
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| UpdateError::Internal)?;
    let current = medication_records::load(&transaction, medication_id)?;
    if current.revision()? != expected_revision {
        return Err(UpdateError::Conflict(format!(
            "expected revision {expected_revision}, current revision is {}",
            current.revision()?
        )));
    }
    let current_json = current.to_json();
    let canonical = canonical_products::open(canonical_db).map_err(UpdateError::from)?;
    let product = resolve_stored_product(&canonical, &current_json)?;
    let changes = update_changes(&payload);
    let merged = merge_values(&current_json, &changes)?;
    let draft = prescriptions::normalize(&merged).map_err(UpdateError::from)?;
    require_explicit_course_bound(&draft)?;
    let draft_hash = prescriptions::draft_hash_optional(
        current_json
            .get("person_id")
            .and_then(Value::as_str)
            .ok_or(UpdateError::Internal)?,
        product.get("product_ref").and_then(Value::as_str),
        &draft,
    )
    .map_err(UpdateError::from)?;

    let bundle = assessment_runtime::evaluate_excluding_medication(
        &canonical,
        &transaction,
        current_json
            .get("person_id")
            .and_then(Value::as_str)
            .ok_or(UpdateError::Internal)?,
        &product,
        &draft,
        acknowledge,
        Some(medication_id),
    )
    .map_err(UpdateError::from)?;
    if bundle.payload_hash != draft_hash {
        return Err(UpdateError::Internal);
    }
    if bundle
        .assessment
        .get("requires_review")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && (!acknowledge || warning_token != bundle.warning_token.as_deref())
    {
        return Err(UpdateError::Confirmation {
            assessment: bundle.assessment,
        });
    }

    let next_revision = expected_revision + 1;
    let draft_object = draft.as_object().ok_or(UpdateError::Internal)?;
    let sql_values = [
        sql_value(draft_object, "dosage_text"),
        sql_value(draft_object, "dose_amount"),
        sql_value(draft_object, "dose_unit"),
        sql_value(draft_object, "frequency_per_day"),
        sql_value(draft_object, "meal_relation"),
        sql_value(draft_object, "administration_route"),
        sql_value(draft_object, "as_needed"),
        sql_value(draft_object, "prn_max_per_day"),
        sql_value(draft_object, "prescription_days"),
        sql_value(draft_object, "long_term"),
        sql_value(draft_object, "start_date"),
        sql_value(draft_object, "end_date"),
        SqlValue::Integer(next_revision),
        SqlValue::Text(medication_id.to_owned()),
        SqlValue::Integer(expected_revision),
    ];
    let changed = transaction
        .execute(
            "UPDATE medications SET dosage_text=?,dose_amount=?,dose_unit=?,frequency_per_day=?,
                meal_relation=?,administration_route=?,as_needed=?,prn_max_per_day=?,prescription_days=?,long_term=?,
                start_date=?,end_date=?,revision=?,updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND revision=?",
            params_from_iter(sql_values.iter()),
        )
        .map_err(|_| UpdateError::Internal)?;
    if changed != 1 {
        return Err(UpdateError::Conflict(
            "medication revision changed during update".to_owned(),
        ));
    }
    replace_schedules(&transaction, medication_id, draft_object)?;
    let today = today_kst();
    transaction
        .execute(
            "DELETE FROM dose_instances WHERE medication_id=? AND status='planned' AND scheduled_date>=?",
            [medication_id, today.as_str()],
        )
        .map_err(|_| UpdateError::Internal)?;

    let updated = medication_records::load(&transaction, medication_id)?;
    let mut snapshot = updated.to_json();
    if let Value::Object(ref mut object) = snapshot {
        object.remove("assessment");
    }
    let assessment = Value::Object(bundle.assessment.clone());
    let snapshot_json = serde_json::to_string(&snapshot).map_err(|_| UpdateError::Internal)?;
    let assessment_json = serde_json::to_string(&assessment).map_err(|_| UpdateError::Internal)?;
    transaction
        .execute(
            "INSERT INTO medication_revisions(
                medication_id,revision,action,snapshot_json,assessment_json,acknowledged,request_id,payload_hash
             ) VALUES(?,?,?,?,?,?,?,?)",
            params_from_iter([
                SqlValue::Text(medication_id.to_owned()),
                SqlValue::Integer(next_revision),
                SqlValue::Text("update".to_owned()),
                SqlValue::Text(snapshot_json),
                SqlValue::Text(assessment_json),
                SqlValue::Integer(i64::from(acknowledge)),
                SqlValue::Null,
                SqlValue::Text(draft_hash.clone()),
            ]),
        )
        .map_err(|_| UpdateError::Internal)?;
    let mut response = updated.to_json();
    if let Value::Object(ref mut object) = response {
        object.insert("assessment".to_owned(), assessment);
    }
    transaction.commit().map_err(|_| UpdateError::Internal)?;
    Ok(response)
}

fn validated_payload(body_json: &str) -> Result<Map<String, Value>, UpdateError> {
    let value = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| UpdateError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let payload = value
        .as_object()
        .cloned()
        .ok_or_else(|| UpdateError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed = UPDATE_FIELDS.into_iter().collect::<BTreeSet<_>>();
    let unknown = payload
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        let mut unknown = unknown;
        unknown.sort();
        return Err(UpdateError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(payload)
}

fn update_changes(payload: &Map<String, Value>) -> Map<String, Value> {
    let mut changes = payload.clone();
    for key in ["expected_revision", "acknowledge_warnings", "warning_token"] {
        changes.remove(key);
    }
    changes
}

fn merge_values(
    current: &Value,
    changes: &Map<String, Value>,
) -> Result<Map<String, Value>, UpdateError> {
    let mut values = Map::new();
    let current = current.as_object().ok_or(UpdateError::Internal)?;
    for key in [
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
        "start_date",
        "end_date",
    ] {
        values.insert(
            key.to_owned(),
            current.get(key).cloned().unwrap_or(Value::Null),
        );
    }
    let schedules = current
        .get("schedules")
        .and_then(Value::as_array)
        .map(|items| {
            Value::Array(
                items
                    .iter()
                    .filter_map(|item| item.get("time_of_day").cloned())
                    .collect(),
            )
        })
        .unwrap_or_else(|| Value::Array(Vec::new()));
    values.insert("schedule_times".to_owned(), schedules);
    for (key, value) in changes {
        values.insert(key.clone(), value.clone());
    }
    if changes.get("as_needed").and_then(Value::as_bool) == Some(true) {
        if !changes.contains_key("schedule_times") {
            values.insert("schedule_times".to_owned(), Value::Array(Vec::new()));
        }
        if !changes.contains_key("frequency_per_day") {
            values.insert("frequency_per_day".to_owned(), Value::Null);
        }
    } else if changes.get("as_needed").and_then(Value::as_bool) == Some(false)
        && !changes.contains_key("prn_max_per_day")
    {
        values.insert("prn_max_per_day".to_owned(), Value::Null);
    }
    if changes.contains_key("schedule_times") && !changes.contains_key("frequency_per_day") {
        values.insert("frequency_per_day".to_owned(), Value::Null);
    }
    if ["prescription_days", "start_date", "long_term"]
        .iter()
        .any(|key| changes.contains_key(*key))
        && !changes.contains_key("end_date")
    {
        values.insert("end_date".to_owned(), Value::Null);
    }
    Ok(values)
}

fn resolve_stored_product(
    canonical: &rusqlite::Connection,
    current: &Value,
) -> Result<Value, UpdateError> {
    let current = current.as_object().ok_or(UpdateError::Internal)?;
    let reference = ["catalog_item_seq", "product_code"].iter().find_map(|key| {
        current
            .get(*key)
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
    });
    if let Some(reference) = reference {
        let mut product = canonical_products::resolve_from_connection(canonical, reference)
            .map_err(UpdateError::from)?;
        product["med_source"] = Value::String("catalog_search".to_owned());
        return Ok(product);
    }
    let name = current
        .get("product_name")
        .and_then(Value::as_str)
        .ok_or(UpdateError::Internal)?
        .trim();
    if name.is_empty() {
        return Err(UpdateError::BadRequest(
            "product_ref, product_code or manual_name is required".to_owned(),
        ));
    }
    Ok(json!({
        "product_ref": null,
        "catalog_item_seq": null,
        "product_code": null,
        "product_name": name,
        "ingredient_code": null,
        "ingredient_name": current.get("ingredient_name").cloned().unwrap_or(Value::Null),
        "manufacturer": null,
        "dosage_form": null,
        "catalog_source": "manual",
        "dur_match": false,
        "dur_coverage_status": "limited",
        "med_source": "manual",
    }))
}

fn replace_schedules(
    con: &rusqlite::Connection,
    medication_id: &str,
    draft: &Map<String, Value>,
) -> Result<(), UpdateError> {
    con.execute(
        "DELETE FROM medication_schedules WHERE medication_id=?",
        [medication_id],
    )
    .map_err(|_| UpdateError::Internal)?;
    let dose_text = sql_value(draft, "dosage_text");
    let schedules = draft
        .get("schedule_times")
        .and_then(Value::as_array)
        .ok_or(UpdateError::Internal)?;
    for time in schedules {
        let time = time
            .as_str()
            .ok_or_else(|| UpdateError::BadRequest("schedule time must be HH:MM".to_owned()))?;
        con.execute(
            "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text) VALUES(?,?,?,?)",
            params_from_iter([
                SqlValue::Text(Uuid::new_v4().to_string()),
                SqlValue::Text(medication_id.to_owned()),
                SqlValue::Text(time.to_owned()),
                dose_text.clone(),
            ]),
        )
        .map_err(|_| UpdateError::Internal)?;
    }
    Ok(())
}

fn sql_value(values: &Map<String, Value>, key: &str) -> SqlValue {
    match values.get(key) {
        None | Some(Value::Null) => SqlValue::Null,
        Some(Value::Bool(value)) => SqlValue::Integer(i64::from(*value)),
        Some(Value::Number(value)) => value
            .as_i64()
            .map(SqlValue::Integer)
            .or_else(|| value.as_f64().map(SqlValue::Real))
            .unwrap_or(SqlValue::Null),
        Some(Value::String(value)) => SqlValue::Text(value.clone()),
        Some(Value::Array(_) | Value::Object(_)) => SqlValue::Null,
    }
}

fn require_explicit_course_bound(draft: &Value) -> Result<(), UpdateError> {
    if draft.get("long_term").and_then(Value::as_bool) == Some(true)
        || draft.get("end_date").is_some_and(|value| !value.is_null())
    {
        return Ok(());
    }
    Err(UpdateError::BadRequest(
        "prescription duration or explicit long_term mode is required".to_owned(),
    ))
}

fn today_kst() -> String {
    Utc::now()
        .with_timezone(&FixedOffset::east_opt(9 * 60 * 60).expect("KST offset"))
        .date_naive()
        .format("%Y-%m-%d")
        .to_string()
}

fn python_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

impl From<ProductError> for UpdateError {
    fn from(error: ProductError) -> Self {
        match error {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::ReferenceUnavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

impl From<AssessmentError> for UpdateError {
    fn from(error: AssessmentError) -> Self {
        match error {
            AssessmentError::BadRequest(detail) => Self::BadRequest(detail),
            AssessmentError::NotFound(detail) => Self::NotFound(detail),
            AssessmentError::PersonalUnavailable => Self::PersonalUnavailable,
            AssessmentError::Internal => Self::Internal,
        }
    }
}

impl From<RecordError> for UpdateError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
        }
    }
}

impl From<DraftError> for UpdateError {
    fn from(error: DraftError) -> Self {
        match error {
            DraftError::BadRequest(detail) => Self::BadRequest(detail),
            DraftError::Internal => Self::Internal,
        }
    }
}

impl From<OpenError> for UpdateError {
    fn from(error: OpenError) -> Self {
        match error {
            OpenError::Unavailable => Self::PersonalUnavailable,
            OpenError::Sql => Self::Internal,
        }
    }
}
