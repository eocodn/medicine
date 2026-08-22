use chrono::{DateTime, FixedOffset, NaiveDateTime, SecondsFormat, Utc};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;
use uuid::Uuid;

use crate::dose_logs;
use crate::personal_db::{self, Access, OpenError};

const DOSE_FIELDS: [&str; 3] = ["status", "occurred_at", "note"];

enum DoseRoute<'a> {
    Record(&'a str),
    Cancel(&'a str),
}

enum DoseError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

#[derive(Clone)]
struct DoseInstance {
    id: String,
    medication_id: String,
    person_id: String,
    scheduled_date: String,
    schedule_key: String,
    scheduled_time: Option<String>,
    slot_label: Option<String>,
    dose_text: Option<String>,
    product_name_snapshot: Option<String>,
    ingredient_name_snapshot: Option<String>,
    status: String,
    completed_at: Option<String>,
    created_at: String,
}

impl DoseInstance {
    fn to_json(&self) -> Value {
        json!({
            "id": self.id,
            "medication_id": self.medication_id,
            "person_id": self.person_id,
            "scheduled_date": self.scheduled_date,
            "schedule_key": self.schedule_key,
            "scheduled_time": self.scheduled_time,
            "slot_label": self.slot_label,
            "dose_text": self.dose_text,
            "product_name_snapshot": self.product_name_snapshot,
            "ingredient_name_snapshot": self.ingredient_name_snapshot,
            "status": self.status,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
        })
    }
}

struct ExistingLog {
    id: String,
}

struct RecordPayload {
    status: String,
    occurred_at: Option<String>,
    note: Option<String>,
    occurred_at_supplied: bool,
    note_supplied: bool,
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
    let route = route(method, path)?;
    let result = match route {
        DoseRoute::Record(instance_id) => record(personal_db, instance_id, body_json),
        DoseRoute::Cancel(instance_id) => cancel(personal_db, instance_id),
    };
    Some(match result {
        Ok(body) => (200, body),
        Err(DoseError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(DoseError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(DoseError::Unavailable) => (503, json!({"detail": "personal database unavailable"})),
        Err(DoseError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<DoseRoute<'a>> {
    let method = method.trim().to_ascii_uppercase();
    let rest = path.strip_prefix("/api/dose-instances/")?;
    if method == "DELETE" {
        let instance_id = rest.strip_suffix("/completion")?;
        if !instance_id.is_empty() && !instance_id.contains('/') {
            return Some(DoseRoute::Cancel(instance_id));
        }
        return None;
    }
    if method == "POST" && !rest.is_empty() && !rest.contains('/') {
        return Some(DoseRoute::Record(rest));
    }
    None
}

fn record(
    personal_db: Option<&Path>,
    instance_id: &str,
    body_json: &str,
) -> Result<Value, DoseError> {
    let payload = record_payload(body_json)?;
    if !matches!(payload.status.as_str(), "taken" | "skipped") {
        return Err(DoseError::BadRequest(
            "status must be taken or skipped".to_owned(),
        ));
    }
    let preserve_existing_same_state = !payload.occurred_at_supplied && !payload.note_supplied;
    let occurred_at = match payload.occurred_at {
        Some(value) => {
            validate_datetime(&value)?;
            value
        }
        None => now_kst(),
    };

    let mut con = open_write(personal_db)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| DoseError::Internal)?;
    let instance = query_instance(&transaction, instance_id)?
        .ok_or_else(|| DoseError::NotFound("dose instance not found".to_owned()))?;
    let existing_log = transaction
        .query_row(
            "SELECT id FROM dose_logs WHERE dose_instance_id=?",
            [instance_id],
            |row| Ok(ExistingLog { id: row.get(0)? }),
        )
        .optional()
        .map_err(|_| DoseError::Internal)?;

    let updated = if preserve_existing_same_state
        && instance.status == payload.status
        && existing_log.is_some()
    {
        instance
    } else {
        transaction
            .execute(
                "UPDATE dose_instances SET status=?, completed_at=? WHERE id=?",
                params![payload.status, occurred_at, instance_id],
            )
            .map_err(|_| DoseError::Internal)?;
        match existing_log {
            None => {
                transaction
                    .execute(
                        "INSERT INTO dose_logs(
                            id,medication_id,person_id,status,occurred_at,note,dose_instance_id,
                            product_name_snapshot,dosage_text_snapshot
                         ) VALUES(?,?,?,?,?,?,?,?,?)",
                        params![
                            Uuid::new_v4().to_string(),
                            instance.medication_id,
                            instance.person_id,
                            payload.status,
                            occurred_at,
                            payload.note,
                            instance_id,
                            instance.product_name_snapshot,
                            instance.dose_text,
                        ],
                    )
                    .map_err(|_| DoseError::Internal)?;
            }
            Some(log) => {
                transaction
                    .execute(
                        "UPDATE dose_logs SET status=?, occurred_at=?, note=? WHERE id=?",
                        params![payload.status, occurred_at, payload.note, log.id],
                    )
                    .map_err(|_| DoseError::Internal)?;
            }
        }
        query_instance(&transaction, instance_id)?.ok_or(DoseError::Internal)?
    };
    let response = with_recent_logs(&transaction, updated.to_json(), &updated.person_id)?;
    transaction.commit().map_err(|_| DoseError::Internal)?;
    Ok(response)
}

fn cancel(personal_db: Option<&Path>, instance_id: &str) -> Result<Value, DoseError> {
    let mut con = open_write(personal_db)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| DoseError::Internal)?;
    let Some(instance) = query_instance(&transaction, instance_id)? else {
        let canceled = transaction
            .query_row(
                "SELECT dose_instance_id,medication_id,person_id
                 FROM prn_requests WHERE dose_instance_id=? AND state='canceled'",
                [instance_id],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                },
            )
            .optional()
            .map_err(|_| DoseError::Internal)?;
        let Some((id, medication_id, person_id)) = canceled else {
            return Err(DoseError::NotFound("dose instance not found".to_owned()));
        };
        let response = with_recent_logs(
            &transaction,
            json!({
                "id": id,
                "medication_id": medication_id,
                "person_id": person_id,
                "status": "canceled",
                "completed_at": null,
                "deleted": true,
            }),
            &person_id,
        )?;
        transaction.commit().map_err(|_| DoseError::Internal)?;
        return Ok(response);
    };

    transaction
        .execute(
            "DELETE FROM dose_logs WHERE dose_instance_id=?",
            [instance_id],
        )
        .map_err(|_| DoseError::Internal)?;
    if instance.schedule_key.starts_with("prn:") {
        transaction
            .execute(
                "UPDATE prn_requests SET state='canceled' WHERE dose_instance_id=?",
                [instance_id],
            )
            .map_err(|_| DoseError::Internal)?;
        transaction
            .execute("DELETE FROM dose_instances WHERE id=?", [instance_id])
            .map_err(|_| DoseError::Internal)?;
        let mut body = instance.to_json();
        if let Value::Object(ref mut object) = body {
            object.insert("status".to_owned(), json!("canceled"));
            object.insert("completed_at".to_owned(), Value::Null);
            object.insert("deleted".to_owned(), Value::Bool(true));
        }
        let response = with_recent_logs(&transaction, body, &instance.person_id)?;
        transaction.commit().map_err(|_| DoseError::Internal)?;
        return Ok(response);
    }

    transaction
        .execute(
            "UPDATE dose_instances SET status='planned', completed_at=NULL WHERE id=?",
            [instance_id],
        )
        .map_err(|_| DoseError::Internal)?;
    let updated = query_instance(&transaction, instance_id)?.ok_or(DoseError::Internal)?;
    let response = with_recent_logs(&transaction, updated.to_json(), &updated.person_id)?;
    transaction.commit().map_err(|_| DoseError::Internal)?;
    Ok(response)
}

fn record_payload(body_json: &str) -> Result<RecordPayload, DoseError> {
    let object = validated_object(body_json)?;
    let status = match object.get("status") {
        None => return Err(DoseError::BadRequest("status is required".to_owned())),
        Some(Value::String(value)) => value.clone(),
        Some(_) => {
            return Err(DoseError::BadRequest(
                "status must be taken or skipped".to_owned(),
            ))
        }
    };
    let occurred_at_supplied = object.contains_key("occurred_at");
    let note_supplied = object.contains_key("note");
    let occurred_at = nullable_string(&object, "occurred_at")?;
    let note = nullable_string(&object, "note")?;
    Ok(RecordPayload {
        status,
        occurred_at,
        note,
        occurred_at_supplied,
        note_supplied,
    })
}

fn validated_object(body_json: &str) -> Result<Map<String, Value>, DoseError> {
    let payload = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| DoseError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let object = payload
        .as_object()
        .ok_or_else(|| DoseError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed: BTreeSet<&str> = DOSE_FIELDS.into_iter().collect();
    let unknown = object
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(DoseError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(object.clone())
}

fn nullable_string(object: &Map<String, Value>, key: &str) -> Result<Option<String>, DoseError> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        Some(_) => Err(DoseError::BadRequest(format!(
            "{key} must be a string or null"
        ))),
    }
}

fn validate_datetime(value: &str) -> Result<(), DoseError> {
    let valid = DateTime::parse_from_rfc3339(value).is_ok()
        || NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M:%S%.f").is_ok()
        || NaiveDateTime::parse_from_str(value, "%Y-%m-%dT%H:%M").is_ok();
    if valid {
        Ok(())
    } else {
        Err(DoseError::BadRequest(
            "invalid occurred_at datetime".to_owned(),
        ))
    }
}

fn now_kst() -> String {
    let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
    Utc::now()
        .with_timezone(&korea)
        .to_rfc3339_opts(SecondsFormat::Secs, false)
}

fn open_write(personal_db_path: Option<&Path>) -> Result<Connection, DoseError> {
    personal_db::open(personal_db_path, Access::ReadWrite).map_err(|error| match error {
        OpenError::Unavailable => DoseError::Unavailable,
        OpenError::Sql => DoseError::Internal,
    })
}

fn query_instance(con: &Connection, instance_id: &str) -> Result<Option<DoseInstance>, DoseError> {
    con.query_row(
        "SELECT id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
                slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status,
                completed_at,created_at
         FROM dose_instances WHERE id=?",
        [instance_id],
        |row| {
            Ok(DoseInstance {
                id: row.get(0)?,
                medication_id: row.get(1)?,
                person_id: row.get(2)?,
                scheduled_date: row.get(3)?,
                schedule_key: row.get(4)?,
                scheduled_time: row.get(5)?,
                slot_label: row.get(6)?,
                dose_text: row.get(7)?,
                product_name_snapshot: row.get(8)?,
                ingredient_name_snapshot: row.get(9)?,
                status: row.get(10)?,
                completed_at: row.get(11)?,
                created_at: row.get(12)?,
            })
        },
    )
    .optional()
    .map_err(|_| DoseError::Internal)
}

fn with_recent_logs(
    con: &Connection,
    mut dose: Value,
    person_id: &str,
) -> Result<Value, DoseError> {
    let logs = dose_logs::recent_logs(con, person_id, 20).map_err(|_| DoseError::Internal)?;
    let Value::Object(ref mut object) = dose else {
        return Err(DoseError::Internal);
    };
    object.insert("recent_logs".to_owned(), Value::Array(logs));
    Ok(dose)
}
