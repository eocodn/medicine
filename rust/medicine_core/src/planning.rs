use chrono::{FixedOffset, NaiveDate, Utc};
use rusqlite::{params, Connection, TransactionBehavior};
use serde_json::{json, Value};
use std::path::Path;
use uuid::Uuid;

use crate::personal_db::{self, Access, OpenError};
use crate::planning_medications::{clock_sort_key, load_active_medications, Medication};

enum PlanningRoute<'a> {
    DailyPlan(&'a str),
}

pub(crate) enum PlanningError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

pub(crate) struct DesiredDose {
    pub(crate) medication_id: String,
    pub(crate) schedule_key: String,
    pub(crate) scheduled_time: Option<String>,
    pub(crate) slot_label: Option<String>,
    pub(crate) dose_text: Option<String>,
}

struct ExistingDose {
    id: String,
    medication_id: String,
    schedule_key: String,
    status: String,
    scheduled_time: Option<String>,
    slot_label: Option<String>,
    dose_text: Option<String>,
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
        PlanningRoute::DailyPlan(person_id) => daily_plan(personal_db, person_id, raw_path),
    };
    Some(match result {
        Ok(body) => (200, body),
        Err(PlanningError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(PlanningError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(PlanningError::Unavailable) => {
            (503, json!({"detail": "personal database unavailable"}))
        }
        Err(PlanningError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<PlanningRoute<'a>> {
    if !method.trim().eq_ignore_ascii_case("GET") {
        return None;
    }
    let person_id = path
        .strip_prefix("/api/people/")?
        .strip_suffix("/daily-plan")?;
    if person_id.is_empty() || person_id.contains('/') {
        return None;
    }
    Some(PlanningRoute::DailyPlan(person_id))
}

fn daily_plan(
    personal_db_path: Option<&Path>,
    person_id: &str,
    raw_path: &str,
) -> Result<Value, PlanningError> {
    let target = target_date(raw_path)?;
    let mut con = open_write(personal_db_path)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| PlanningError::Internal)?;
    if !person_exists(&transaction, person_id)? {
        return Err(PlanningError::NotFound("person not found".to_owned()));
    }
    let medications = load_active_medications(&transaction, person_id, target)?;
    let plan = materialize(&transaction, person_id, &medications, target)?;
    transaction.commit().map_err(|_| PlanningError::Internal)?;
    Ok(plan)
}

pub(crate) fn materialize_for_dashboard(
    con: &Connection,
    person_id: &str,
    medications: &[Medication],
    target: NaiveDate,
) -> Result<Value, PlanningError> {
    materialize(con, person_id, medications, target)
}

pub(crate) fn target_date(raw_path: &str) -> Result<NaiveDate, PlanningError> {
    match query_parameter(raw_path, "date")? {
        Some(value) => parse_date(&value),
        None => Ok(today_kst()),
    }
}

fn open_write(personal_db_path: Option<&Path>) -> Result<Connection, PlanningError> {
    personal_db::open(personal_db_path, Access::ReadWrite).map_err(|error| match error {
        OpenError::Unavailable => PlanningError::Unavailable,
        OpenError::Sql => PlanningError::Internal,
    })
}

fn person_exists(con: &Connection, person_id: &str) -> Result<bool, PlanningError> {
    con.query_row(
        "SELECT EXISTS(SELECT 1 FROM people WHERE id=?)",
        [person_id],
        |row| row.get::<_, i64>(0),
    )
    .map(|exists| exists != 0)
    .map_err(|_| PlanningError::Internal)
}

fn materialize(
    con: &Connection,
    person_id: &str,
    medications: &[Medication],
    target: NaiveDate,
) -> Result<Value, PlanningError> {
    let applicable = medications
        .iter()
        .filter_map(|medication| match medication_applies(medication, target) {
            Ok(true) => Some(Ok(medication)),
            Ok(false) => None,
            Err(error) => Some(Err(error)),
        })
        .collect::<Result<Vec<_>, _>>()?;
    let prn = applicable
        .iter()
        .copied()
        .filter(|medication| medication.as_needed())
        .collect::<Vec<_>>();
    let scheduled = applicable
        .iter()
        .copied()
        .filter(|medication| !medication.as_needed())
        .collect::<Vec<_>>();
    let unscheduled = scheduled
        .iter()
        .copied()
        .filter(|medication| {
            medication.schedules.is_empty() && medication.frequency_per_day().unwrap_or(0) == 0
        })
        .collect::<Vec<_>>();
    let mut desired = Vec::new();
    for medication in &scheduled {
        desired.extend(desired_for(medication)?);
    }

    reconcile_instances(con, person_id, &scheduled, target, &desired)?;
    let doses = plan_doses(con, person_id, target)?;
    let summary = json!({
        "planned": doses.iter().filter(|dose| dose["status"] == "planned").count(),
        "taken": doses.iter().filter(|dose| dose["status"] == "taken").count(),
        "skipped": doses.iter().filter(|dose| dose["status"] == "skipped").count(),
    });
    Ok(json!({
        "date": target.format("%Y-%m-%d").to_string(),
        "doses": doses,
        "prn_medications": prn.into_iter().map(Medication::to_json).collect::<Vec<_>>(),
        "unscheduled_medications": unscheduled.into_iter().map(Medication::to_json).collect::<Vec<_>>(),
        "summary": summary,
    }))
}

pub(crate) fn medication_applies(
    medication: &Medication,
    target: NaiveDate,
) -> Result<bool, PlanningError> {
    if !medication.active() {
        return Ok(false);
    }
    if medication.start_date()?.is_some_and(|start| target < start) {
        return Ok(false);
    }
    if medication.end_date()?.is_some_and(|end| target > end) {
        return Ok(false);
    }
    Ok(true)
}

pub(crate) fn desired_for(medication: &Medication) -> Result<Vec<DesiredDose>, PlanningError> {
    let medication_id = medication.id()?.to_owned();
    if !medication.schedules.is_empty() {
        return medication
            .schedules
            .iter()
            .enumerate()
            .map(|(index, schedule)| {
                Ok(DesiredDose {
                    medication_id: medication_id.clone(),
                    schedule_key: format!("slot:{}", index + 1),
                    scheduled_time: Some(schedule.time_of_day.clone()),
                    slot_label: None,
                    dose_text: schedule
                        .dose_text
                        .clone()
                        .or_else(|| medication.dosage_text().map(str::to_owned)),
                })
            })
            .collect();
    }
    let frequency = medication.frequency_per_day().unwrap_or(0);
    if frequency <= 0 {
        return Ok(Vec::new());
    }
    Ok((1..=frequency)
        .map(|index| DesiredDose {
            medication_id: medication_id.clone(),
            schedule_key: format!("slot:{index}"),
            scheduled_time: None,
            slot_label: Some(format!("{index}회차")),
            dose_text: medication.dosage_text().map(str::to_owned),
        })
        .collect::<Vec<_>>())
}

fn reconcile_instances(
    con: &Connection,
    person_id: &str,
    scheduled: &[&Medication],
    target: NaiveDate,
    desired: &[DesiredDose],
) -> Result<(), PlanningError> {
    let target_text = target.format("%Y-%m-%d").to_string();
    let mut statement = con
        .prepare(
            "SELECT id,medication_id,schedule_key,status,scheduled_time,slot_label,dose_text
             FROM dose_instances WHERE person_id=? AND scheduled_date=?",
        )
        .map_err(|_| PlanningError::Internal)?;
    let existing = statement
        .query_map(params![person_id, target_text], |row| {
            Ok(ExistingDose {
                id: row.get(0)?,
                medication_id: row.get(1)?,
                schedule_key: row.get(2)?,
                status: row.get(3)?,
                scheduled_time: row.get(4)?,
                slot_label: row.get(5)?,
                dose_text: row.get(6)?,
            })
        })
        .map_err(|_| PlanningError::Internal)?
        .map(|row| row.map_err(|_| PlanningError::Internal))
        .collect::<Result<Vec<_>, _>>()?;

    for row in &existing {
        let desired_key = desired.iter().any(|item| {
            item.medication_id == row.medication_id && item.schedule_key == row.schedule_key
        });
        if row.status == "planned" && !desired_key {
            con.execute("DELETE FROM dose_instances WHERE id=?", [&row.id])
                .map_err(|_| PlanningError::Internal)?;
        }
    }

    for item in desired {
        let current = existing.iter().find(|row| {
            row.medication_id == item.medication_id && row.schedule_key == item.schedule_key
        });
        match current {
            None => {
                let medication = scheduled
                    .iter()
                    .copied()
                    .find(|medication| medication.id().ok() == Some(item.medication_id.as_str()))
                    .ok_or(PlanningError::Internal)?;
                con.execute(
                    "INSERT OR IGNORE INTO dose_instances(
                        id,medication_id,person_id,scheduled_date,schedule_key,scheduled_time,
                        slot_label,dose_text,product_name_snapshot,ingredient_name_snapshot,status
                     ) VALUES(?,?,?,?,?,?,?,?,?,?, 'planned')",
                    params![
                        Uuid::new_v4().to_string(),
                        item.medication_id,
                        person_id,
                        target_text,
                        item.schedule_key,
                        item.scheduled_time,
                        item.slot_label,
                        item.dose_text,
                        medication.product_name()?,
                        medication.ingredient_name(),
                    ],
                )
                .map_err(|_| PlanningError::Internal)?;
            }
            Some(current) if current.status != "planned" => {}
            Some(current)
                if current.scheduled_time == item.scheduled_time
                    && current.slot_label == item.slot_label
                    && current.dose_text == item.dose_text => {}
            Some(current) => {
                con.execute(
                    "UPDATE dose_instances SET scheduled_time=?,slot_label=?,dose_text=?
                     WHERE id=? AND status='planned'",
                    params![
                        item.scheduled_time,
                        item.slot_label,
                        item.dose_text,
                        current.id
                    ],
                )
                .map_err(|_| PlanningError::Internal)?;
            }
        }
    }
    Ok(())
}

fn plan_doses(
    con: &Connection,
    person_id: &str,
    target: NaiveDate,
) -> Result<Vec<Value>, PlanningError> {
    let target_text = target.format("%Y-%m-%d").to_string();
    let mut statement = con
        .prepare(
            "SELECT i.id,i.medication_id,i.person_id,i.scheduled_date,i.schedule_key,
                    i.scheduled_time,i.slot_label,i.dose_text,i.product_name_snapshot,
                    i.ingredient_name_snapshot,i.status,i.completed_at,i.created_at,
                    COALESCE(i.product_name_snapshot,m.product_name) AS product_name,
                    COALESCE(i.ingredient_name_snapshot,m.ingredient_name) AS ingredient_name,
                    m.meal_relation,m.administration_route
             FROM dose_instances i JOIN medications m ON m.id=i.medication_id
             WHERE i.person_id=? AND i.scheduled_date=? AND i.schedule_key NOT LIKE 'prn:%'",
        )
        .map_err(|_| PlanningError::Internal)?;
    let rows = statement
        .query_map(params![person_id, target_text], |row| {
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
                "product_name": row.get::<_, String>(13)?,
                "ingredient_name": row.get::<_, Option<String>>(14)?,
                "meal_relation": row.get::<_, Option<String>>(15)?.unwrap_or_else(|| "unspecified".to_owned()),
                "administration_route": row.get::<_, Option<String>>(16)?.unwrap_or_else(|| "unknown".to_owned()),
            }))
        })
        .map_err(|_| PlanningError::Internal)?
        .map(|row| row.map_err(|_| PlanningError::Internal))
        .collect::<Result<Vec<_>, _>>()?;
    let mut keyed = rows
        .into_iter()
        .map(|dose| {
            let key = if let Some(time) = dose["scheduled_time"].as_str() {
                let (hour, minute) = clock_sort_key(time)?;
                (0_u32, hour, minute)
            } else {
                let slot = dose["schedule_key"]
                    .as_str()
                    .and_then(|key| key.split_once(':'))
                    .and_then(|(_, slot)| slot.parse::<u32>().ok())
                    .ok_or(PlanningError::Internal)?;
                (1_u32, slot, 0_u32)
            };
            Ok((key, dose))
        })
        .collect::<Result<Vec<_>, PlanningError>>()?;
    keyed.sort_by_key(|(key, _)| *key);
    Ok(keyed.into_iter().map(|(_, dose)| dose).collect())
}

fn parse_date(value: &str) -> Result<NaiveDate, PlanningError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|_| PlanningError::BadRequest(format!("Invalid isoformat string: '{value}'")))
}

fn today_kst() -> NaiveDate {
    let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
    Utc::now().with_timezone(&korea).date_naive()
}

fn query_parameter(raw_path: &str, key: &str) -> Result<Option<String>, PlanningError> {
    let Some((_, raw_query)) = raw_path.split_once('?') else {
        return Ok(None);
    };
    let query = raw_query
        .split_once('#')
        .map_or(raw_query, |(query, _)| query);
    let mut result = None;
    for pair in query.split('&') {
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        if percent_decode(raw_key)? != key {
            continue;
        }
        let value = percent_decode(raw_value)?;
        result = Some(value);
    }
    Ok(result)
}

fn percent_decode(value: &str) -> Result<String, PlanningError> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' if index + 2 < bytes.len() => {
                let high = hex_value(bytes[index + 1]).ok_or_else(|| {
                    PlanningError::BadRequest("invalid query encoding".to_owned())
                })?;
                let low = hex_value(bytes[index + 2]).ok_or_else(|| {
                    PlanningError::BadRequest("invalid query encoding".to_owned())
                })?;
                decoded.push((high << 4) | low);
                index += 2;
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    String::from_utf8(decoded)
        .map_err(|_| PlanningError::BadRequest("invalid query encoding".to_owned()))
}

fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}
