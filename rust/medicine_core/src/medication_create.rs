use rusqlite::{params, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;
use uuid::Uuid;

use crate::assessment_runtime::{self, AssessmentError};
use crate::canonical_products::{self, ProductError};
use crate::medication_records::{self, RecordError};
use crate::personal_db::{self, Access, OpenError};
use crate::prescriptions::{self, DraftError};

const CREATE_FIELDS: [&str; 20] = [
    "product_ref",
    "product_code",
    "manual_name",
    "ingredient_name",
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
    "request_id",
    "acknowledge_warnings",
    "warning_token",
];

#[derive(Debug)]
enum CreateError {
    BadRequest(String),
    Conflict(String),
    Confirmation {
        request_id: Option<String>,
        assessment: Map<String, Value>,
    },
    NotFound(String),
    ReferenceUnavailable,
    PersonalUnavailable,
    Internal,
}

pub(crate) fn handle(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    person_id: &str,
    body_json: &str,
) -> (u16, Value) {
    match create(canonical_db, personal_db, person_id, body_json) {
        Ok(body) => (201, body),
        Err(CreateError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(CreateError::Conflict(detail)) => (409, json!({"detail": detail})),
        Err(CreateError::Confirmation {
            request_id,
            assessment,
        }) => {
            let warning_token = assessment
                .get("warning_token")
                .cloned()
                .unwrap_or(Value::Null);
            (
                409,
                json!({
                    "confirmation_required": true,
                    "request_id": request_id,
                    "warning_token": warning_token,
                    "assessment": assessment,
                }),
            )
        }
        Err(CreateError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(CreateError::ReferenceUnavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(CreateError::PersonalUnavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(CreateError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn create(
    canonical_db: Option<&Path>,
    personal_db: Option<&Path>,
    person_id: &str,
    body_json: &str,
) -> Result<Value, CreateError> {
    let payload = validated_payload(body_json)?;
    let canonical = canonical_products::open(canonical_db).map_err(CreateError::from)?;
    let product = resolve_product(&canonical, &payload)?;
    require_active_permit(&product)?;
    let draft = normalized_draft(&payload)?;
    require_explicit_course_bound(&draft)?;

    let product_ref = product.get("product_ref").and_then(Value::as_str);
    let payload_hash = prescriptions::draft_hash_optional(person_id, product_ref, &draft)
        .map_err(CreateError::from)?;
    let request_id = optional_trimmed_string(&payload, "request_id")?;
    let acknowledged = payload
        .get("acknowledge_warnings")
        .is_some_and(python_truthy);
    let warning_token = payload.get("warning_token").and_then(Value::as_str);

    let mut personal =
        personal_db::open(personal_db, Access::ReadWrite).map_err(CreateError::from)?;
    let transaction = personal
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CreateError::Internal)?;

    if let Some(request_id) = request_id.as_deref() {
        let existing = transaction
            .query_row(
                "SELECT person_id,payload_hash,medication_id
                 FROM medication_requests WHERE request_id=?",
                [request_id],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                },
            )
            .optional()
            .map_err(|_| CreateError::Internal)?;
        if let Some((existing_person, existing_hash, medication_id)) = existing {
            if existing_person != person_id || existing_hash != payload_hash {
                return Err(CreateError::Conflict(
                    "request_id was already used with a different prescription payload".to_owned(),
                ));
            }
            return medication_records::load(&transaction, &medication_id)
                .map(|medication| medication.to_json())
                .map_err(CreateError::from);
        }
    }

    let bundle = assessment_runtime::evaluate(
        &canonical,
        &transaction,
        person_id,
        &product,
        &draft,
        acknowledged,
    )
    .map_err(CreateError::from)?;
    if bundle.payload_hash != payload_hash {
        return Err(CreateError::Internal);
    }
    let requires_review = bundle
        .assessment
        .get("requires_review")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if requires_review && (!acknowledged || warning_token != bundle.warning_token.as_deref()) {
        return Err(CreateError::Confirmation {
            request_id,
            assessment: bundle.assessment,
        });
    }

    let medication_id = Uuid::new_v4().to_string();
    insert_medication(&transaction, &medication_id, person_id, &product, &draft)?;
    insert_schedules(&transaction, &medication_id, &draft)?;

    let mut medication = medication_records::load(&transaction, &medication_id)?;
    let mut snapshot = medication.to_json();
    if let Value::Object(ref mut object) = snapshot {
        object.remove("assessment");
    }
    let assessment = Value::Object(bundle.assessment.clone());
    transaction
        .execute(
            "INSERT INTO medication_revisions(
                medication_id,revision,action,snapshot_json,assessment_json,acknowledged,
                request_id,payload_hash
             ) VALUES(?,1,'create',?,?,?,?,?)",
            params![
                medication_id,
                serde_json::to_string(&snapshot).map_err(|_| CreateError::Internal)?,
                serde_json::to_string(&assessment).map_err(|_| CreateError::Internal)?,
                i64::from(acknowledged),
                request_id,
                payload_hash,
            ],
        )
        .map_err(|_| CreateError::Internal)?;
    if let Some(request_id) = request_id.as_deref() {
        transaction
            .execute(
                "INSERT INTO medication_requests(request_id,person_id,payload_hash,medication_id)
                 VALUES(?,?,?,?)",
                params![request_id, person_id, payload_hash, medication_id],
            )
            .map_err(|_| CreateError::Internal)?;
    }
    medication.insert("assessment", assessment);
    let response = medication.to_json();
    transaction.commit().map_err(|_| CreateError::Internal)?;
    Ok(response)
}

fn validated_payload(body_json: &str) -> Result<Map<String, Value>, CreateError> {
    let value = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| CreateError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let payload = value
        .as_object()
        .cloned()
        .ok_or_else(|| CreateError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed = CREATE_FIELDS.into_iter().collect::<BTreeSet<_>>();
    let unknown = payload
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(CreateError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(payload)
}

fn resolve_product(
    canonical: &rusqlite::Connection,
    payload: &Map<String, Value>,
) -> Result<Value, CreateError> {
    for key in ["product_ref", "product_code"] {
        if let Some(value) = payload.get(key).and_then(Value::as_str) {
            if !value.is_empty() {
                let mut product = canonical_products::resolve_from_connection(canonical, value)
                    .map_err(CreateError::from)?;
                product["med_source"] = Value::String("catalog_search".to_owned());
                return Ok(product);
            }
        }
    }
    let manual_name = optional_string(payload, "manual_name")?
        .unwrap_or("")
        .trim();
    if manual_name.is_empty() {
        return Err(CreateError::BadRequest(
            "product_ref, product_code or manual_name is required".to_owned(),
        ));
    }
    let ingredient_name = optional_string(payload, "ingredient_name")?;
    Ok(json!({
        "product_ref": null,
        "catalog_item_seq": null,
        "product_code": null,
        "product_name": manual_name,
        "ingredient_code": null,
        "ingredient_name": ingredient_name,
        "manufacturer": null,
        "dosage_form": null,
        "catalog_source": "manual",
        "dur_match": false,
        "dur_coverage_status": "limited",
        "med_source": "manual",
    }))
}

fn require_active_permit(product: &Value) -> Result<(), CreateError> {
    if product.get("catalog_source").and_then(Value::as_str) != Some("canonical") {
        return Ok(());
    }
    if product.get("permit_status").and_then(Value::as_str) == Some("active") {
        return Ok(());
    }
    Err(CreateError::BadRequest(
        "inactive permit product cannot be added to the current medication regimen".to_owned(),
    ))
}

fn normalized_draft(payload: &Map<String, Value>) -> Result<Value, CreateError> {
    let mut draft = payload.clone();
    for key in [
        "product_ref",
        "product_code",
        "manual_name",
        "ingredient_name",
        "ingredient_code",
        "request_id",
        "acknowledge_warnings",
        "warning_token",
    ] {
        draft.remove(key);
    }
    prescriptions::normalize(&draft).map_err(CreateError::from)
}

fn require_explicit_course_bound(draft: &Value) -> Result<(), CreateError> {
    if draft.get("long_term").and_then(Value::as_bool) == Some(true)
        || draft.get("end_date").is_some_and(|value| !value.is_null())
    {
        return Ok(());
    }
    Err(CreateError::BadRequest(
        "prescription duration or explicit long_term mode is required".to_owned(),
    ))
}

fn insert_medication(
    con: &rusqlite::Connection,
    medication_id: &str,
    person_id: &str,
    product: &Value,
    draft: &Value,
) -> Result<(), CreateError> {
    let product = product.as_object().ok_or(CreateError::Internal)?;
    let draft = draft.as_object().ok_or(CreateError::Internal)?;
    let product_name = required_value_string(product, "product_name")?;
    let catalog_source = required_value_string(product, "catalog_source")?;
    let med_source = required_value_string(product, "med_source")?;
    let meal_relation = required_value_string(draft, "meal_relation")?;
    let administration_route = required_value_string(draft, "administration_route")?;
    let start_date = required_value_string(draft, "start_date")?;
    con.execute(
        "INSERT INTO medications(
            id,person_id,catalog_item_seq,product_code,product_name,ingredient_code,ingredient_name,
            manufacturer,catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,
            meal_relation,administration_route,as_needed,prn_max_per_day,prescription_days,long_term,
            start_date,end_date,active,source,revision
         ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1)",
        params![
            medication_id,
            person_id,
            value_string(product, "catalog_item_seq")?,
            value_string(product, "product_code")?,
            product_name,
            value_string(product, "ingredient_code")?,
            value_string(product, "ingredient_name")?,
            value_string(product, "manufacturer")?,
            catalog_source,
            value_string(draft, "dosage_text")?,
            value_string(draft, "dose_amount")?,
            value_string(draft, "dose_unit")?,
            draft.get("frequency_per_day").and_then(Value::as_i64),
            meal_relation,
            administration_route,
            i64::from(draft.get("as_needed").and_then(Value::as_bool).unwrap_or(false)),
            draft.get("prn_max_per_day").and_then(Value::as_i64),
            draft.get("prescription_days").and_then(Value::as_i64),
            i64::from(draft.get("long_term").and_then(Value::as_bool).unwrap_or(false)),
            start_date,
            value_string(draft, "end_date")?,
            med_source,
        ],
    )
    .map_err(|_| CreateError::Internal)?;
    Ok(())
}

fn insert_schedules(
    con: &rusqlite::Connection,
    medication_id: &str,
    draft: &Value,
) -> Result<(), CreateError> {
    let schedules = draft
        .get("schedule_times")
        .and_then(Value::as_array)
        .ok_or(CreateError::Internal)?;
    let dose_text = value_string(
        draft.as_object().ok_or(CreateError::Internal)?,
        "dosage_text",
    )?;
    for time in schedules {
        let time = time.as_str().ok_or(CreateError::Internal)?;
        con.execute(
            "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text)
             VALUES(?,?,?,?)",
            params![Uuid::new_v4().to_string(), medication_id, time, dose_text],
        )
        .map_err(|_| CreateError::Internal)?;
    }
    Ok(())
}

fn optional_trimmed_string(
    payload: &Map<String, Value>,
    key: &str,
) -> Result<Option<String>, CreateError> {
    Ok(optional_string(payload, key)?
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned))
}

fn optional_string<'a>(
    payload: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, CreateError> {
    match payload.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        Some(_) => Err(CreateError::BadRequest(format!("{key} must be a string"))),
    }
}

fn required_value_string<'a>(
    payload: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a str, CreateError> {
    value_string(payload, key)?.ok_or(CreateError::Internal)
}

fn value_string<'a>(
    payload: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, CreateError> {
    match payload.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        Some(_) => Err(CreateError::Internal),
    }
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

impl From<ProductError> for CreateError {
    fn from(error: ProductError) -> Self {
        match error {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::ReferenceUnavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

impl From<AssessmentError> for CreateError {
    fn from(error: AssessmentError) -> Self {
        match error {
            AssessmentError::BadRequest(detail) => Self::BadRequest(detail),
            AssessmentError::NotFound(detail) => Self::NotFound(detail),
            AssessmentError::PersonalUnavailable => Self::PersonalUnavailable,
            AssessmentError::Internal => Self::Internal,
        }
    }
}

impl From<RecordError> for CreateError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
        }
    }
}

impl From<DraftError> for CreateError {
    fn from(error: DraftError) -> Self {
        match error {
            DraftError::BadRequest(detail) => Self::BadRequest(detail),
            DraftError::Internal => Self::Internal,
        }
    }
}

impl From<OpenError> for CreateError {
    fn from(error: OpenError) -> Self {
        match error {
            OpenError::Unavailable => Self::PersonalUnavailable,
            OpenError::Sql => Self::Internal,
        }
    }
}
