use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, SecondsFormat, Utc};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::Path;
use uuid::Uuid;

use crate::dose_logs;
use crate::personal_db::{self, Access, OpenError};

const PRN_FIELDS: [&str; 3] = ["occurred_at", "note", "request_id"];

enum PrnError {
    BadRequest(String),
    Conflict(String),
    NotFound(String),
    Unavailable,
    Internal,
}

struct PrnPayload {
    request_id: String,
    occurred_at: Option<String>,
    note: Option<String>,
}

struct Medication {
    id: String,
    person_id: String,
    product_name: String,
    ingredient_name: Option<String>,
    dosage_text: Option<String>,
    start_date: Option<String>,
    end_date: Option<String>,
    active: bool,
    as_needed: bool,
    prn_max_per_day: Option<i64>,
}

struct ExistingRequest {
    medication_id: String,
    payload_hash: String,
    dose_instance_id: String,
    state: String,
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    route(method, path).is_some()
}

pub(crate) fn handle_request(
    personal_db: Option<&Path>,
    method: &str,
    path: &str,
    body_json: &str,
) -> Option<(u16, Value)> {
    let medication_id = route(method, path)?;
    let result = record(personal_db, medication_id, body_json);
    Some(match result {
        Ok(body) => (201, body),
        Err(PrnError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(PrnError::Conflict(detail)) => (409, json!({"detail": detail})),
        Err(PrnError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(PrnError::Unavailable) => (503, json!({"detail": "personal database unavailable"})),
        Err(PrnError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<&'a str> {
    if !method.trim().eq_ignore_ascii_case("POST") {
        return None;
    }
    let medication_id = path
        .strip_prefix("/api/medications/")?
        .strip_suffix("/prn-intakes")?;
    if medication_id.is_empty() || medication_id.contains('/') {
        return None;
    }
    Some(medication_id)
}

fn record(
    personal_db_path: Option<&Path>,
    medication_id: &str,
    body_json: &str,
) -> Result<Value, PrnError> {
    let payload = parse_payload(body_json)?;
    let payload_hash = request_payload_hash(medication_id, &payload.occurred_at, &payload.note);
    let occurred_at = payload.occurred_at.clone().unwrap_or_else(now_kst);
    let target = occurrence_date(&occurred_at)?;

    let mut con = open_write(personal_db_path)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| PrnError::Internal)?;

    if let Some(existing) = query_request(&transaction, &payload.request_id)? {
        if existing.medication_id != medication_id || existing.payload_hash != payload_hash {
            return Err(PrnError::Conflict(
                "request_id was already used with a different PRN intake payload".to_owned(),
            ));
        }
        if existing.state != "active" {
            return Err(PrnError::Conflict(
                "request_id refers to a canceled PRN intake".to_owned(),
            ));
        }
        let instance =
            query_instance(&transaction, &existing.dose_instance_id)?.ok_or(PrnError::Internal)?;
        let response = with_recent_logs(
            &transaction,
            instance,
            &instance_person_id(&transaction, &existing.dose_instance_id)?,
        )?;
        transaction.commit().map_err(|_| PrnError::Internal)?;
        return Ok(response);
    }

    let medication = query_medication(&transaction, medication_id)?
        .ok_or_else(|| PrnError::NotFound("medication not found".to_owned()))?;
    if !medication_applies_on(&medication, target)? {
        return Err(PrnError::BadRequest(
            "PRN medication is not active on the intake date".to_owned(),
        ));
    }
    if !medication.as_needed {
        return Err(PrnError::BadRequest(
            "medication is not PRN/as_needed".to_owned(),
        ));
    }
    if let Some(maximum) = medication.prn_max_per_day {
        if prn_taken_count(&transaction, medication_id, target)? >= maximum {
            return Err(PrnError::BadRequest(
                "PRN daily maximum has already been reached".to_owned(),
            ));
        }
    }

    let instance_id = Uuid::new_v4().to_string();
    transaction
        .execute(
            "INSERT INTO dose_instances(
                id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
                slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status
             ) VALUES(?,?,?,?,?,NULL,?,?,?,?, 'planned')",
            params![
                instance_id,
                medication.id,
                medication.person_id,
                target.format("%Y-%m-%d").to_string(),
                format!("prn:{instance_id}"),
                "필요시",
                medication.dosage_text,
                medication.product_name,
                medication.ingredient_name,
            ],
        )
        .map_err(|_| PrnError::Internal)?;
    transaction
        .execute(
            "UPDATE dose_instances SET status='taken',completed_at=? WHERE id=?",
            params![occurred_at, instance_id],
        )
        .map_err(|_| PrnError::Internal)?;
    transaction
        .execute(
            "INSERT INTO dose_logs(
                id,medication_id,person_id,status,occurred_at,note,dose_instance_id,
                product_name_snapshot,dosage_text_snapshot
             ) VALUES(?,?,?,'taken',?,?,?,?,?)",
            params![
                Uuid::new_v4().to_string(),
                medication_id,
                medication.person_id,
                occurred_at,
                payload.note,
                instance_id,
                medication.product_name,
                medication.dosage_text,
            ],
        )
        .map_err(|_| PrnError::Internal)?;
    transaction
        .execute(
            "INSERT INTO prn_requests(
                request_id,medication_id,person_id,payload_hash,dose_instance_id,state
             ) VALUES(?,?,?,?,?,'active')",
            params![
                payload.request_id,
                medication_id,
                medication.person_id,
                payload_hash,
                instance_id,
            ],
        )
        .map_err(|_| PrnError::Internal)?;

    let instance = query_instance(&transaction, &instance_id)?.ok_or(PrnError::Internal)?;
    let response = with_recent_logs(&transaction, instance, &medication.person_id)?;
    transaction.commit().map_err(|_| PrnError::Internal)?;
    Ok(response)
}

fn parse_payload(body_json: &str) -> Result<PrnPayload, PrnError> {
    let object = validated_object(body_json)?;
    let request_id = match object.get("request_id") {
        Some(Value::String(value)) => value.trim().to_owned(),
        None | Some(Value::Null) => String::new(),
        Some(value) => json_scalar_string(value)
            .unwrap_or_default()
            .trim()
            .to_owned(),
    };
    if request_id.is_empty() {
        return Err(PrnError::BadRequest("request_id is required".to_owned()));
    }
    Ok(PrnPayload {
        request_id,
        occurred_at: nullable_string(&object, "occurred_at")?,
        note: nullable_string(&object, "note")?,
    })
}

fn validated_object(body_json: &str) -> Result<Map<String, Value>, PrnError> {
    let payload = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| PrnError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let object = payload
        .as_object()
        .ok_or_else(|| PrnError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed: BTreeSet<&str> = PRN_FIELDS.into_iter().collect();
    let unknown = object
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(PrnError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(object.clone())
}

fn nullable_string(object: &Map<String, Value>, key: &str) -> Result<Option<String>, PrnError> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        Some(_) => Err(PrnError::BadRequest(format!(
            "{key} must be a string or null"
        ))),
    }
}

fn json_scalar_string(value: &Value) -> Option<String> {
    match value {
        Value::Bool(true) => Some("True".to_owned()),
        Value::Bool(false) => Some("False".to_owned()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn request_payload_hash(
    medication_id: &str,
    occurred_at: &Option<String>,
    note: &Option<String>,
) -> String {
    let payload = json!({
        "medication_id": medication_id,
        "note": note,
        "occurred_at": occurred_at,
    });
    let bytes = serde_json::to_vec(&payload).expect("PRN payload JSON is serializable");
    let digest = Sha256::digest(bytes);
    format!("{digest:x}")
}

fn open_write(personal_db_path: Option<&Path>) -> Result<Connection, PrnError> {
    personal_db::open(personal_db_path, Access::ReadWrite).map_err(|error| match error {
        OpenError::Unavailable => PrnError::Unavailable,
        OpenError::Sql => PrnError::Internal,
    })
}

fn query_request(con: &Connection, request_id: &str) -> Result<Option<ExistingRequest>, PrnError> {
    con.query_row(
        "SELECT medication_id,payload_hash,dose_instance_id,state
         FROM prn_requests WHERE request_id=?",
        [request_id],
        |row| {
            Ok(ExistingRequest {
                medication_id: row.get(0)?,
                payload_hash: row.get(1)?,
                dose_instance_id: row.get(2)?,
                state: row.get(3)?,
            })
        },
    )
    .optional()
    .map_err(|_| PrnError::Internal)
}

fn query_medication(con: &Connection, medication_id: &str) -> Result<Option<Medication>, PrnError> {
    con.query_row(
        "SELECT id,person_id,product_name,ingredient_name,dosage_text,start_date,end_date,
                active,as_needed,prn_max_per_day
         FROM medications WHERE id=?",
        [medication_id],
        |row| {
            Ok(Medication {
                id: row.get(0)?,
                person_id: row.get(1)?,
                product_name: row.get(2)?,
                ingredient_name: row.get(3)?,
                dosage_text: row.get(4)?,
                start_date: row.get(5)?,
                end_date: row.get(6)?,
                active: row.get::<_, i64>(7)? != 0,
                as_needed: row.get::<_, i64>(8)? != 0,
                prn_max_per_day: row.get(9)?,
            })
        },
    )
    .optional()
    .map_err(|_| PrnError::Internal)
}

fn medication_applies_on(medication: &Medication, target: NaiveDate) -> Result<bool, PrnError> {
    if !medication.active {
        return Ok(false);
    }
    if let Some(start) = medication.start_date.as_deref() {
        if target < parse_date(start)? {
            return Ok(false);
        }
    }
    if let Some(end) = medication.end_date.as_deref() {
        if target > parse_date(end)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn prn_taken_count(
    con: &Connection,
    medication_id: &str,
    target: NaiveDate,
) -> Result<i64, PrnError> {
    let mut statement = con
        .prepare(
            "SELECT l.occurred_at FROM dose_logs l
             JOIN dose_instances i ON i.id=l.dose_instance_id
             WHERE l.medication_id=? AND l.status='taken' AND i.schedule_key LIKE 'prn:%'",
        )
        .map_err(|_| PrnError::Internal)?;
    let rows = statement
        .query_map([medication_id], |row| row.get::<_, String>(0))
        .map_err(|_| PrnError::Internal)?;
    let mut count = 0;
    for row in rows {
        let occurred_at = row.map_err(|_| PrnError::Internal)?;
        if occurrence_date(&occurred_at).ok() == Some(target) {
            count += 1;
        }
    }
    Ok(count)
}

fn query_instance(con: &Connection, instance_id: &str) -> Result<Option<Value>, PrnError> {
    con.query_row(
        "SELECT id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
                slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status,
                completed_at,created_at
         FROM dose_instances WHERE id=?",
        [instance_id],
        |row| {
            Ok(json!({
                "id": row.get::<_, String>(0)?,
                "medication_id": row.get::<_, String>(1)?,
                "person_id": row.get::<_, String>(2)?,
                "scheduled_date": row.get::<_, String>(3)?,
                "schedule_key": row.get::<_, String>(4)?,
                "scheduled_time": row.get::<_, Option<String>>(5)?,
                "slot_label": row.get::<_, Option<String>>(6)?,
                "dose_text": row.get::<_, Option<String>>(7)?,
                "product_name_snapshot": row.get::<_, Option<String>>(8)?,
                "ingredient_name_snapshot": row.get::<_, Option<String>>(9)?,
                "status": row.get::<_, String>(10)?,
                "completed_at": row.get::<_, Option<String>>(11)?,
                "created_at": row.get::<_, String>(12)?,
            }))
        },
    )
    .optional()
    .map_err(|_| PrnError::Internal)
}

fn instance_person_id(con: &Connection, instance_id: &str) -> Result<String, PrnError> {
    con.query_row(
        "SELECT person_id FROM dose_instances WHERE id=?",
        [instance_id],
        |row| row.get(0),
    )
    .map_err(|_| PrnError::Internal)
}

fn with_recent_logs(
    con: &Connection,
    mut instance: Value,
    person_id: &str,
) -> Result<Value, PrnError> {
    let logs = dose_logs::recent_logs(con, person_id, 20).map_err(|_| PrnError::Internal)?;
    instance
        .as_object_mut()
        .ok_or(PrnError::Internal)?
        .insert("recent_logs".to_owned(), Value::Array(logs));
    Ok(instance)
}

fn occurrence_date(value: &str) -> Result<NaiveDate, PrnError> {
    if let Ok(aware) = DateTime::parse_from_rfc3339(value) {
        let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
        return Ok(aware.with_timezone(&korea).date_naive());
    }
    for format in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(value, format) {
            return Ok(naive.date());
        }
    }
    Err(PrnError::BadRequest(format!(
        "Invalid isoformat string: '{value}'"
    )))
}

fn parse_date(value: &str) -> Result<NaiveDate, PrnError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|_| PrnError::BadRequest(format!("Invalid isoformat string: '{value}'")))
}

fn now_kst() -> String {
    let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
    Utc::now()
        .with_timezone(&korea)
        .to_rfc3339_opts(SecondsFormat::Secs, false)
}
