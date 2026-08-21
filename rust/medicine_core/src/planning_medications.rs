use chrono::NaiveDate;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::cmp::Ordering;

use crate::medication_records::{self, RecordError};
use crate::planning::PlanningError;

pub(crate) use crate::medication_records::{clock_sort_key, Medication};

impl From<RecordError> for PlanningError {
    fn from(error: RecordError) -> Self {
        match error {
            RecordError::BadRequest(detail) => Self::BadRequest(detail),
            RecordError::NotFound | RecordError::Internal => Self::Internal,
        }
    }
}

pub(crate) fn load_active_medications(
    con: &Connection,
    person_id: &str,
    target: NaiveDate,
) -> Result<Vec<Medication>, PlanningError> {
    let medications = medication_records::load_for_person(con, person_id, true)?;
    let mut keyed = Vec::new();
    for mut medication in medications {
        medication.insert("course_progress", course_progress(&medication, target)?);
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
