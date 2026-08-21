use chrono::NaiveDate;
use rusqlite::types::{Type, ValueRef};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Number, Value};
use std::cmp::Ordering;

use crate::planning::PlanningError;

#[derive(Clone)]
pub(crate) struct Schedule {
    pub(crate) time_of_day: String,
    pub(crate) dose_text: Option<String>,
}

impl Schedule {
    fn to_json(&self) -> Value {
        json!({
            "time_of_day": self.time_of_day,
            "dose_text": self.dose_text,
        })
    }
}

#[derive(Clone)]
pub(crate) struct Medication {
    data: Map<String, Value>,
    pub(crate) schedules: Vec<Schedule>,
}

impl Medication {
    pub(crate) fn id(&self) -> Result<&str, PlanningError> {
        self.string("id")
    }

    pub(crate) fn product_name(&self) -> Result<&str, PlanningError> {
        self.string("product_name")
    }

    pub(crate) fn ingredient_name(&self) -> Option<&str> {
        self.optional_string("ingredient_name")
    }

    pub(crate) fn dosage_text(&self) -> Option<&str> {
        self.optional_string("dosage_text")
    }

    pub(crate) fn start_date(&self) -> Result<Option<NaiveDate>, PlanningError> {
        optional_date(self.optional_string("start_date"))
    }

    pub(crate) fn end_date(&self) -> Result<Option<NaiveDate>, PlanningError> {
        optional_date(self.optional_string("end_date"))
    }

    pub(crate) fn active(&self) -> bool {
        self.boolean("active")
    }

    pub(crate) fn as_needed(&self) -> bool {
        self.boolean("as_needed")
    }

    pub(crate) fn frequency_per_day(&self) -> Option<i64> {
        self.data.get("frequency_per_day").and_then(Value::as_i64)
    }

    pub(crate) fn to_json(&self) -> Value {
        let mut data = self.data.clone();
        data.insert(
            "schedules".to_owned(),
            Value::Array(self.schedules.iter().map(Schedule::to_json).collect()),
        );
        Value::Object(data)
    }

    fn string(&self, key: &str) -> Result<&str, PlanningError> {
        self.data
            .get(key)
            .and_then(Value::as_str)
            .ok_or(PlanningError::Internal)
    }

    fn optional_string(&self, key: &str) -> Option<&str> {
        self.data.get(key).and_then(Value::as_str)
    }

    fn boolean(&self, key: &str) -> bool {
        self.data.get(key).and_then(Value::as_bool).unwrap_or(false)
    }
}

pub(crate) fn load_active_medications(
    con: &Connection,
    person_id: &str,
    target: NaiveDate,
) -> Result<Vec<Medication>, PlanningError> {
    let mut statement = con
        .prepare("SELECT * FROM medications WHERE person_id=? AND active=1 ORDER BY rowid")
        .map_err(|_| PlanningError::Internal)?;
    let column_names = statement
        .column_names()
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let rows = statement
        .query_map([person_id], |row| row_to_map(row, &column_names))
        .map_err(|_| PlanningError::Internal)?;
    let mut keyed = Vec::new();
    for row in rows {
        let mut data = row.map_err(|_| PlanningError::Internal)?;
        normalize_medication(&mut data)?;
        let medication_id = data
            .get("id")
            .and_then(Value::as_str)
            .ok_or(PlanningError::Internal)?
            .to_owned();
        let schedules = load_schedules(con, &medication_id)?;
        attach_assessment(con, &mut data, &medication_id)?;
        let mut medication = Medication { data, schedules };
        medication.data.insert(
            "course_progress".to_owned(),
            course_progress(&medication, target)?,
        );
        if medication.end_date()?.is_some_and(|end| end < target) {
            continue;
        }
        let sort_key = earliest_schedule(&medication)?
            .map_or((1_u32, 0_u32, 0_u32), |(hour, minute)| (0, hour, minute));
        keyed.push((sort_key, medication));
    }
    keyed.sort_by_key(|(key, _)| *key);
    Ok(keyed
        .into_iter()
        .map(|(_, medication)| medication)
        .collect())
}

pub(crate) fn clock_sort_key(value: &str) -> Result<(u32, u32), PlanningError> {
    let Some((hour, minute)) = value.split_once(':') else {
        return Err(PlanningError::BadRequest(
            "schedule time must be HH:MM".to_owned(),
        ));
    };
    let hour = hour
        .parse::<u32>()
        .map_err(|_| PlanningError::BadRequest("schedule time must be HH:MM".to_owned()))?;
    let minute = minute
        .parse::<u32>()
        .map_err(|_| PlanningError::BadRequest("schedule time must be HH:MM".to_owned()))?;
    if hour > 23 || minute > 59 {
        return Err(PlanningError::BadRequest(
            "schedule time must be HH:MM".to_owned(),
        ));
    }
    Ok((hour, minute))
}

fn row_to_map(row: &Row<'_>, columns: &[String]) -> rusqlite::Result<Map<String, Value>> {
    let mut data = Map::new();
    for (index, name) in columns.iter().enumerate() {
        let value = match row.get_ref(index)? {
            ValueRef::Null => Value::Null,
            ValueRef::Integer(value) => Value::Number(Number::from(value)),
            ValueRef::Real(value) => Number::from_f64(value).map_or(Value::Null, Value::Number),
            ValueRef::Text(value) => Value::String(String::from_utf8_lossy(value).into_owned()),
            ValueRef::Blob(_) => {
                return Err(rusqlite::Error::InvalidColumnType(
                    index,
                    name.clone(),
                    Type::Blob,
                ));
            }
        };
        data.insert(name.clone(), value);
    }
    Ok(data)
}

fn normalize_medication(data: &mut Map<String, Value>) -> Result<(), PlanningError> {
    for key in ["active", "as_needed", "long_term"] {
        let enabled = match data.get(key) {
            Some(Value::Bool(value)) => *value,
            Some(Value::Number(value)) => value.as_i64().unwrap_or(0) != 0,
            Some(Value::Null) | None => false,
            _ => return Err(PlanningError::Internal),
        };
        data.insert(key.to_owned(), Value::Bool(enabled));
    }
    normalize_default_string(data, "meal_relation", "unspecified");
    normalize_default_string(data, "administration_route", "unknown");
    Ok(())
}

fn normalize_default_string(data: &mut Map<String, Value>, key: &str, default: &str) {
    let missing = data
        .get(key)
        .and_then(Value::as_str)
        .is_none_or(str::is_empty);
    if missing {
        data.insert(key.to_owned(), Value::String(default.to_owned()));
    }
}

fn load_schedules(con: &Connection, medication_id: &str) -> Result<Vec<Schedule>, PlanningError> {
    let mut statement = con
        .prepare(
            "SELECT time_of_day,dose_text FROM medication_schedules
             WHERE medication_id=? ORDER BY time_of_day",
        )
        .map_err(|_| PlanningError::Internal)?;
    let rows = statement
        .query_map([medication_id], |row| {
            Ok(Schedule {
                time_of_day: row.get(0)?,
                dose_text: row.get(1)?,
            })
        })
        .map_err(|_| PlanningError::Internal)?;
    let mut keyed = rows
        .map(|row| {
            let schedule = row.map_err(|_| PlanningError::Internal)?;
            Ok((clock_sort_key(&schedule.time_of_day)?, schedule))
        })
        .collect::<Result<Vec<_>, PlanningError>>()?;
    keyed.sort_by_key(|(key, _)| *key);
    Ok(keyed.into_iter().map(|(_, schedule)| schedule).collect())
}

fn attach_assessment(
    con: &Connection,
    data: &mut Map<String, Value>,
    medication_id: &str,
) -> Result<(), PlanningError> {
    let revision = data.get("revision").and_then(Value::as_i64).unwrap_or(1);
    let assessment = con
        .query_row(
            "SELECT assessment_json FROM medication_revisions WHERE medication_id=? AND revision=?",
            params![medication_id, revision],
            |row| row.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(|_| PlanningError::Internal)?
        .flatten();
    if let Some(assessment) = assessment {
        let value = serde_json::from_str(&assessment).map_err(|_| PlanningError::Internal)?;
        data.insert("assessment".to_owned(), value);
    }
    Ok(())
}

fn earliest_schedule(medication: &Medication) -> Result<Option<(u32, u32)>, PlanningError> {
    let mut earliest = None;
    for schedule in &medication.schedules {
        let key = clock_sort_key(&schedule.time_of_day)?;
        earliest = Some(earliest.map_or(key, |current: (u32, u32)| current.min(key)));
    }
    Ok(earliest)
}

fn course_progress(medication: &Medication, target: NaiveDate) -> Result<Value, PlanningError> {
    let (Some(start), Some(end)) = (medication.start_date()?, medication.end_date()?) else {
        return Ok(Value::Null);
    };
    let total_days = (end - start).num_days() + 1;
    if total_days <= 0 {
        return Err(PlanningError::BadRequest(
            "medication end_date must be on or after start_date".to_owned(),
        ));
    }
    let (status, current_day, remaining_days, progress_percent) = if target < start {
        ("upcoming", 0, total_days, 0)
    } else if target > end {
        ("completed", total_days, 0, 100)
    } else {
        let current_day = (target - start).num_days() + 1;
        let remaining_days = (end - target).num_days();
        (
            "active",
            current_day,
            remaining_days,
            round_ratio_even(current_day * 100, total_days),
        )
    };
    Ok(json!({
        "status": status,
        "total_days": total_days,
        "current_day": current_day,
        "remaining_days": remaining_days,
        "progress_percent": progress_percent,
    }))
}

fn round_ratio_even(numerator: i64, denominator: i64) -> i64 {
    let quotient = numerator / denominator;
    let remainder = numerator % denominator;
    match (remainder * 2).cmp(&denominator) {
        Ordering::Less => quotient,
        Ordering::Greater => quotient + 1,
        Ordering::Equal if quotient % 2 == 0 => quotient,
        Ordering::Equal => quotient + 1,
    }
}

fn optional_date(value: Option<&str>) -> Result<Option<NaiveDate>, PlanningError> {
    value.map(parse_date).transpose()
}

fn parse_date(value: &str) -> Result<NaiveDate, PlanningError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|_| PlanningError::BadRequest(format!("Invalid isoformat string: '{value}'")))
}
