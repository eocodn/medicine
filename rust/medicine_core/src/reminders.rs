use chrono::{DateTime, Duration, FixedOffset, NaiveDate, TimeZone};
use rusqlite::{params, OptionalExtension, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;

use crate::personal_db::{self, Access, OpenError};
use crate::planning::{self, PlanningError};
use crate::planning_medications::load_active_medications;

const RESOLVE_FIELDS: [&str; 5] = [
    "person_id",
    "medication_id",
    "scheduled_date",
    "schedule_key",
    "scheduled_at",
];
const MAX_LOOKAHEAD_DAYS: i64 = 31;

enum ReminderRoute {
    Upcoming,
    Resolve,
}

enum ReminderError {
    BadRequest(String),
    Unavailable,
    Internal,
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    route(method, path).is_some()
}

pub(crate) fn handle_request(
    personal_db: Option<&Path>,
    method: &str,
    raw_path: &str,
    path: &str,
    body_json: &str,
) -> Option<(u16, Value)> {
    let route = route(method, path)?;
    let result = match route {
        ReminderRoute::Upcoming => upcoming(personal_db, raw_path),
        ReminderRoute::Resolve => resolve(personal_db, body_json),
    };
    Some(match result {
        Ok(body) => (200, body),
        Err(ReminderError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(ReminderError::Unavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(ReminderError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route(method: &str, path: &str) -> Option<ReminderRoute> {
    let method = method.trim().to_ascii_uppercase();
    match (method.as_str(), path) {
        ("GET", "/api/reminders/upcoming") => Some(ReminderRoute::Upcoming),
        ("POST", "/api/reminders/resolve") => Some(ReminderRoute::Resolve),
        _ => None,
    }
}

fn upcoming(personal_db_path: Option<&Path>, raw_path: &str) -> Result<Value, ReminderError> {
    let from = query_parameter(raw_path, "from")?
        .ok_or_else(|| ReminderError::BadRequest("from is required".to_owned()))?;
    let from = DateTime::parse_from_rfc3339(&from)
        .map_err(|_| ReminderError::BadRequest("from must be RFC3339".to_owned()))?;
    let days = query_parameter(raw_path, "days")?
        .ok_or_else(|| ReminderError::BadRequest("days is required".to_owned()))?
        .parse::<i64>()
        .map_err(|_| ReminderError::BadRequest("days must be an integer".to_owned()))?;
    if !(1..=MAX_LOOKAHEAD_DAYS).contains(&days) {
        return Err(ReminderError::BadRequest(format!(
            "days must be between 1 and {MAX_LOOKAHEAD_DAYS}"
        )));
    }

    let korea = korea_offset();
    let from_kst = from.with_timezone(&korea);
    let con = personal_db::open(personal_db_path, Access::ReadOnly).map_err(map_open)?;
    let person_ids = {
        let mut statement = con
            .prepare("SELECT id FROM people ORDER BY rowid")
            .map_err(|_| ReminderError::Internal)?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|_| ReminderError::Internal)?
            .map(|row| row.map_err(|_| ReminderError::Internal))
            .collect::<Result<Vec<_>, _>>()?;
        rows
    };

    let mut occurrences = Vec::new();
    for day_offset in 0..days {
        let date = from_kst.date_naive() + Duration::days(day_offset);
        for person_id in &person_ids {
            let medications =
                load_active_medications(&con, person_id, date).map_err(map_planning)?;
            for medication in medications {
                if medication.as_needed()
                    || !planning::medication_applies(&medication, date).map_err(map_planning)?
                {
                    continue;
                }
                for desired in planning::desired_for(&medication).map_err(map_planning)? {
                    let Some(time) = desired.scheduled_time.as_deref() else {
                        continue;
                    };
                    let scheduled_at = scheduled_at(date, time)?;
                    if scheduled_at <= from_kst {
                        continue;
                    }
                    let existing_status = con
                        .query_row(
                            "SELECT status FROM dose_instances
                             WHERE medication_id=? AND scheduled_date=? AND schedule_key=?",
                            params![
                                desired.medication_id,
                                date.format("%Y-%m-%d").to_string(),
                                desired.schedule_key,
                            ],
                            |row| row.get::<_, String>(0),
                        )
                        .optional()
                        .map_err(|_| ReminderError::Internal)?;
                    if existing_status
                        .as_deref()
                        .is_some_and(|status| status != "planned")
                    {
                        continue;
                    }
                    occurrences.push(json!({
                        "person_id": person_id,
                        "medication_id": desired.medication_id,
                        "scheduled_date": date.format("%Y-%m-%d").to_string(),
                        "schedule_key": desired.schedule_key,
                        "scheduled_at": scheduled_at.to_rfc3339(),
                    }));
                }
            }
        }
    }
    occurrences.sort_by(|left, right| {
        left["scheduled_at"]
            .as_str()
            .cmp(&right["scheduled_at"].as_str())
            .then_with(|| left["person_id"].as_str().cmp(&right["person_id"].as_str()))
            .then_with(|| {
                left["medication_id"]
                    .as_str()
                    .cmp(&right["medication_id"].as_str())
            })
    });
    Ok(json!({"occurrences": occurrences}))
}

fn resolve(personal_db_path: Option<&Path>, body_json: &str) -> Result<Value, ReminderError> {
    let payload = validated_resolve_payload(body_json)?;
    let person_id = required_string(&payload, "person_id")?;
    let medication_id = required_string(&payload, "medication_id")?;
    let scheduled_date_text = required_string(&payload, "scheduled_date")?;
    let schedule_key = required_string(&payload, "schedule_key")?;
    let expected_scheduled_at =
        DateTime::parse_from_rfc3339(required_string(&payload, "scheduled_at")?)
            .map_err(|_| ReminderError::BadRequest("scheduled_at must be RFC3339".to_owned()))?;
    let scheduled_date = NaiveDate::parse_from_str(scheduled_date_text, "%Y-%m-%d")
        .map_err(|_| ReminderError::BadRequest("scheduled_date must be YYYY-MM-DD".to_owned()))?;

    let mut con = personal_db::open(personal_db_path, Access::ReadWrite).map_err(map_open)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| ReminderError::Internal)?;
    let person_name = transaction
        .query_row("SELECT name FROM people WHERE id=?", [person_id], |row| {
            row.get::<_, String>(0)
        })
        .optional()
        .map_err(|_| ReminderError::Internal)?;
    let Some(person_name) = person_name else {
        transaction.commit().map_err(|_| ReminderError::Internal)?;
        return Ok(json!({"active": false}));
    };
    let medications =
        load_active_medications(&transaction, person_id, scheduled_date).map_err(map_planning)?;
    let plan =
        planning::materialize_for_dashboard(&transaction, person_id, &medications, scheduled_date)
            .map_err(map_planning)?;
    let resolved = plan["doses"]
        .as_array()
        .and_then(|doses| {
            doses.iter().find(|dose| {
                dose["medication_id"] == medication_id && dose["schedule_key"] == schedule_key
            })
        })
        .cloned();
    let response = match resolved {
        Some(dose) if dose["status"] == "planned" && dose["scheduled_time"].is_string() => {
            let time = dose["scheduled_time"]
                .as_str()
                .ok_or(ReminderError::Internal)?;
            let current_scheduled_at = scheduled_at(scheduled_date, time)?;
            if current_scheduled_at != expected_scheduled_at {
                json!({"active": false})
            } else {
                json!({
                    "active": true,
                    "dose_instance_id": dose["id"],
                    "person_id": person_id,
                    "person_name": person_name,
                    "medication_id": medication_id,
                    "product_name": dose["product_name"],
                    "dose_text": dose["dose_text"],
                    "scheduled_date": scheduled_date_text,
                    "schedule_key": schedule_key,
                    "scheduled_at": current_scheduled_at.to_rfc3339(),
                })
            }
        }
        _ => json!({"active": false}),
    };
    transaction.commit().map_err(|_| ReminderError::Internal)?;
    Ok(response)
}

fn scheduled_at(date: NaiveDate, time: &str) -> Result<DateTime<FixedOffset>, ReminderError> {
    let (hour, minute) = crate::medication_records::clock_sort_key(time).map_err(map_record)?;
    let naive = date
        .and_hms_opt(hour, minute, 0)
        .ok_or_else(|| ReminderError::BadRequest("schedule time must be HH:MM".to_owned()))?;
    korea_offset()
        .from_local_datetime(&naive)
        .single()
        .ok_or(ReminderError::Internal)
}

fn korea_offset() -> FixedOffset {
    FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid")
}

fn validated_resolve_payload(body_json: &str) -> Result<Map<String, Value>, ReminderError> {
    let payload: Value = serde_json::from_str(body_json)
        .map_err(|_| ReminderError::BadRequest("request body must be valid JSON".to_owned()))?;
    let object = payload.as_object().ok_or_else(|| {
        ReminderError::BadRequest("request body must be a JSON object".to_owned())
    })?;
    let allowed: BTreeSet<&str> = RESOLVE_FIELDS.into_iter().collect();
    let unknown = object
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(ReminderError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(object.clone())
}

fn required_string<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a str, ReminderError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ReminderError::BadRequest(format!("{key} is required")))
}

fn query_parameter(raw_path: &str, key: &str) -> Result<Option<String>, ReminderError> {
    let Some((_, raw_query)) = raw_path.split_once('?') else {
        return Ok(None);
    };
    let query = raw_query
        .split_once('#')
        .map_or(raw_query, |(query, _)| query);
    let mut result = None;
    for pair in query.split('&') {
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        if percent_decode(raw_key)? == key {
            result = Some(percent_decode(raw_value)?);
        }
    }
    Ok(result)
}

fn percent_decode(value: &str) -> Result<String, ReminderError> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' if index + 2 < bytes.len() => {
                let high = hex_value(bytes[index + 1]).ok_or_else(|| {
                    ReminderError::BadRequest("invalid query encoding".to_owned())
                })?;
                let low = hex_value(bytes[index + 2]).ok_or_else(|| {
                    ReminderError::BadRequest("invalid query encoding".to_owned())
                })?;
                decoded.push((high << 4) | low);
                index += 2;
            }
            b'%' => {
                return Err(ReminderError::BadRequest(
                    "invalid query encoding".to_owned(),
                ))
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    String::from_utf8(decoded)
        .map_err(|_| ReminderError::BadRequest("invalid query encoding".to_owned()))
}

fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn map_open(error: OpenError) -> ReminderError {
    match error {
        OpenError::Unavailable => ReminderError::Unavailable,
        OpenError::Sql => ReminderError::Internal,
    }
}

fn map_planning(error: PlanningError) -> ReminderError {
    match error {
        PlanningError::BadRequest(detail) => ReminderError::BadRequest(detail),
        PlanningError::NotFound(_) => ReminderError::Internal,
        PlanningError::Unavailable => ReminderError::Unavailable,
        PlanningError::Internal => ReminderError::Internal,
    }
}

fn map_record(error: crate::medication_records::RecordError) -> ReminderError {
    match error {
        crate::medication_records::RecordError::BadRequest(detail) => {
            ReminderError::BadRequest(detail)
        }
        crate::medication_records::RecordError::NotFound
        | crate::medication_records::RecordError::Internal => ReminderError::Internal,
    }
}
