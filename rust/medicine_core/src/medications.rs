use chrono::{FixedOffset, Utc};
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};
use std::path::Path;

use crate::medication_records::{self, RecordError};
use crate::personal_db::{self, Access, OpenError};

enum MedicationRoute<'a> {
    History(&'a str),
    Stop(&'a str),
}

enum MedicationError {
    BadRequest(String),
    Conflict(String),
    NotFound(String),
    Unavailable,
    Internal,
}

impl From<RecordError> for MedicationError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound => Self::NotFound("medication not found".to_owned()),
            RecordError::Internal => Self::Internal,
        }
    }
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    route(method, path).is_some()
}

pub(crate) fn handle_request(
    personal_db: Option<&Path>,
    method: &str,
    raw_path: &str,
    path: &str,
) -> Option<(u16, Value)> {
    let route = route(method, path)?;
    let result = match route {
        MedicationRoute::History(medication_id) => history(personal_db, medication_id),
        MedicationRoute::Stop(medication_id) => stop(personal_db, medication_id, raw_path),
    };
    Some(match result {
        Ok(body) => (200, body),
        Err(MedicationError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(MedicationError::Conflict(detail)) => (409, json!({"detail": detail})),
        Err(MedicationError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(MedicationError::Unavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(MedicationError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<MedicationRoute<'a>> {
    let rest = path.strip_prefix("/api/medications/")?;
    if method.trim().eq_ignore_ascii_case("GET") {
        let medication_id = rest.strip_suffix("/history")?;
        if !medication_id.is_empty() && !medication_id.contains('/') {
            return Some(MedicationRoute::History(medication_id));
        }
        return None;
    }
    if method.trim().eq_ignore_ascii_case("DELETE") && !rest.is_empty() && !rest.contains('/') {
        return Some(MedicationRoute::Stop(rest));
    }
    None
}

fn history(personal_db_path: Option<&Path>, medication_id: &str) -> Result<Value, MedicationError> {
    let con = open(personal_db_path, Access::ReadOnly)?;
    medication_records::load(&con, medication_id)?;
    let mut statement = con
        .prepare(
            "SELECT medication_id,revision,action,snapshot_json,assessment_json,acknowledged,
                    request_id,payload_hash,created_at
             FROM medication_revisions WHERE medication_id=? ORDER BY revision",
        )
        .map_err(|_| MedicationError::Internal)?;
    let rows = statement
        .query_map([medication_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, i64>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, Option<String>>(6)?,
                row.get::<_, Option<String>>(7)?,
                row.get::<_, String>(8)?,
            ))
        })
        .map_err(|_| MedicationError::Internal)?;
    let mut result = Vec::new();
    for row in rows {
        let (
            medication_id,
            revision,
            action,
            snapshot_json,
            assessment_json,
            acknowledged,
            request_id,
            payload_hash,
            created_at,
        ) = row.map_err(|_| MedicationError::Internal)?;
        let snapshot =
            serde_json::from_str::<Value>(&snapshot_json).map_err(|_| MedicationError::Internal)?;
        let assessment = assessment_json
            .as_deref()
            .map(serde_json::from_str::<Value>)
            .transpose()
            .map_err(|_| MedicationError::Internal)?;
        result.push(json!({
            "medication_id": medication_id,
            "revision": revision,
            "action": action,
            "acknowledged": acknowledged != 0,
            "request_id": request_id,
            "payload_hash": payload_hash,
            "created_at": created_at,
            "snapshot": snapshot,
            "assessment": assessment,
        }));
    }
    Ok(Value::Array(result))
}

fn stop(
    personal_db_path: Option<&Path>,
    medication_id: &str,
    raw_path: &str,
) -> Result<Value, MedicationError> {
    let expected_revision = expected_revision(raw_path)?;
    let mut con = open(personal_db_path, Access::ReadWrite)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| MedicationError::Internal)?;
    let current = medication_records::load(&transaction, medication_id)?;
    let current_revision = current.revision()?;
    if let Some(expected) = expected_revision {
        if expected != current_revision {
            return Err(MedicationError::Conflict(format!(
                "expected revision {expected}, current revision is {current_revision}"
            )));
        }
    }

    let next_revision = current_revision + 1;
    let today = today_kst();
    let stopped_at = current.stopped_at().unwrap_or(&today).to_owned();
    let changed = transaction
        .execute(
            "UPDATE medications
             SET active=0,stopped_at=?,revision=?,updated_at=CURRENT_TIMESTAMP
             WHERE id=? AND revision=?",
            params![stopped_at, next_revision, medication_id, current_revision],
        )
        .map_err(|_| MedicationError::Internal)?;
    if changed != 1 {
        return Err(MedicationError::Conflict(
            "medication revision changed during stop".to_owned(),
        ));
    }
    transaction
        .execute(
            "DELETE FROM dose_instances
             WHERE medication_id=? AND status='planned' AND scheduled_date>=?",
            params![medication_id, today],
        )
        .map_err(|_| MedicationError::Internal)?;

    // Load before appending the new revision. The existing Python contract
    // therefore returns the stopped record without an `assessment` field while
    // the stop revision inherits the assessment from the previous revision.
    let stopped = medication_records::load(&transaction, medication_id)?;
    let assessment = current.assessment().cloned().unwrap_or_else(|| json!({}));
    let mut snapshot = stopped.to_json();
    if let Value::Object(ref mut object) = snapshot {
        object.remove("assessment");
    }
    transaction
        .execute(
            "INSERT INTO medication_revisions(
                medication_id,revision,action,snapshot_json,assessment_json,acknowledged,
                request_id,payload_hash
             ) VALUES(?,?,?,?,?,0,NULL,NULL)",
            params![
                medication_id,
                next_revision,
                "stop",
                serde_json::to_string(&snapshot).map_err(|_| MedicationError::Internal)?,
                serde_json::to_string(&assessment).map_err(|_| MedicationError::Internal)?,
            ],
        )
        .map_err(|_| MedicationError::Internal)?;
    let response = stopped.to_json();
    transaction
        .commit()
        .map_err(|_| MedicationError::Internal)?;
    Ok(response)
}

fn expected_revision(raw_path: &str) -> Result<Option<i64>, MedicationError> {
    let Some((_, raw_query)) = raw_path.split_once('?') else {
        return Ok(None);
    };
    let query = raw_query
        .split_once('#')
        .map_or(raw_query, |(query, _)| query);
    let mut found = None;
    for pair in query.split('&') {
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        if percent_decode(raw_key)? == "expected_revision" {
            found = Some(percent_decode(raw_value)?);
        }
    }
    let Some(value) = found else {
        return Ok(None);
    };
    value
        .trim()
        .parse::<i64>()
        .map(Some)
        .map_err(|_| MedicationError::BadRequest("expected_revision must be an integer".to_owned()))
}

fn percent_decode(value: &str) -> Result<String, MedicationError> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' if index + 2 < bytes.len() => {
                let high = hex_value(bytes[index + 1]).ok_or_else(|| {
                    MedicationError::BadRequest("invalid query encoding".to_owned())
                })?;
                let low = hex_value(bytes[index + 2]).ok_or_else(|| {
                    MedicationError::BadRequest("invalid query encoding".to_owned())
                })?;
                decoded.push((high << 4) | low);
                index += 2;
            }
            b'%' => {
                return Err(MedicationError::BadRequest(
                    "invalid query encoding".to_owned(),
                ));
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    String::from_utf8(decoded)
        .map_err(|_| MedicationError::BadRequest("invalid query encoding".to_owned()))
}

fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn open(personal_db_path: Option<&Path>, access: Access) -> Result<Connection, MedicationError> {
    personal_db::open(personal_db_path, access).map_err(|error| match error {
        OpenError::Unavailable => MedicationError::Unavailable,
        OpenError::Sql => MedicationError::Internal,
    })
}

fn today_kst() -> String {
    let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
    Utc::now()
        .with_timezone(&korea)
        .date_naive()
        .format("%Y-%m-%d")
        .to_string()
}
