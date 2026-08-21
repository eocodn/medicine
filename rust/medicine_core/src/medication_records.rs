use chrono::NaiveDate;
use rusqlite::types::{Type, ValueRef};
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::{json, Map, Number, Value};

#[derive(Debug)]
pub(crate) enum RecordError {
    BadRequest(String),
    NotFound,
    Internal,
}

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
    pub(crate) fn id(&self) -> Result<&str, RecordError> {
        self.string("id")
    }

    pub(crate) fn product_name(&self) -> Result<&str, RecordError> {
        self.string("product_name")
    }

    pub(crate) fn ingredient_name(&self) -> Option<&str> {
        self.optional_string("ingredient_name")
    }

    pub(crate) fn dosage_text(&self) -> Option<&str> {
        self.optional_string("dosage_text")
    }

    pub(crate) fn start_date(&self) -> Result<Option<NaiveDate>, RecordError> {
        optional_date(self.optional_string("start_date"))
    }

    pub(crate) fn end_date(&self) -> Result<Option<NaiveDate>, RecordError> {
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

    pub(crate) fn revision(&self) -> Result<i64, RecordError> {
        self.data
            .get("revision")
            .and_then(Value::as_i64)
            .ok_or(RecordError::Internal)
    }

    pub(crate) fn stopped_at(&self) -> Option<&str> {
        self.optional_string("stopped_at")
    }

    pub(crate) fn assessment(&self) -> Option<&Value> {
        self.data.get("assessment")
    }

    pub(crate) fn insert(&mut self, key: &str, value: Value) {
        self.data.insert(key.to_owned(), value);
    }

    pub(crate) fn to_json(&self) -> Value {
        let mut data = self.data.clone();
        data.insert(
            "schedules".to_owned(),
            Value::Array(self.schedules.iter().map(Schedule::to_json).collect()),
        );
        Value::Object(data)
    }

    fn string(&self, key: &str) -> Result<&str, RecordError> {
        self.data
            .get(key)
            .and_then(Value::as_str)
            .ok_or(RecordError::Internal)
    }

    fn optional_string(&self, key: &str) -> Option<&str> {
        self.data.get(key).and_then(Value::as_str)
    }

    fn boolean(&self, key: &str) -> bool {
        self.data.get(key).and_then(Value::as_bool).unwrap_or(false)
    }
}

pub(crate) fn load(con: &Connection, medication_id: &str) -> Result<Medication, RecordError> {
    let mut statement = con
        .prepare("SELECT * FROM medications WHERE id=?")
        .map_err(|_| RecordError::Internal)?;
    let column_names = statement
        .column_names()
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let data = statement
        .query_row([medication_id], |row| row_to_map(row, &column_names))
        .optional()
        .map_err(|_| RecordError::Internal)?
        .ok_or(RecordError::NotFound)?;
    finish_record(con, data)
}

pub(crate) fn load_for_person(
    con: &Connection,
    person_id: &str,
    active_only: bool,
) -> Result<Vec<Medication>, RecordError> {
    let sql = if active_only {
        "SELECT id FROM medications WHERE person_id=? AND active=1 ORDER BY rowid"
    } else {
        "SELECT id FROM medications WHERE person_id=? ORDER BY rowid"
    };
    let mut statement = con.prepare(sql).map_err(|_| RecordError::Internal)?;
    let ids = statement
        .query_map([person_id], |row| row.get::<_, String>(0))
        .map_err(|_| RecordError::Internal)?
        .map(|row| row.map_err(|_| RecordError::Internal))
        .collect::<Result<Vec<_>, _>>()?;
    ids.into_iter()
        .map(|medication_id| load(con, &medication_id))
        .collect()
}

pub(crate) fn clock_sort_key(value: &str) -> Result<(u32, u32), RecordError> {
    let Some((hour, minute)) = value.split_once(':') else {
        return Err(RecordError::BadRequest(
            "schedule time must be HH:MM".to_owned(),
        ));
    };
    let hour = hour
        .parse::<u32>()
        .map_err(|_| RecordError::BadRequest("schedule time must be HH:MM".to_owned()))?;
    let minute = minute
        .parse::<u32>()
        .map_err(|_| RecordError::BadRequest("schedule time must be HH:MM".to_owned()))?;
    if hour > 23 || minute > 59 {
        return Err(RecordError::BadRequest(
            "schedule time must be HH:MM".to_owned(),
        ));
    }
    Ok((hour, minute))
}

fn finish_record(
    con: &Connection,
    mut data: Map<String, Value>,
) -> Result<Medication, RecordError> {
    normalize_medication(&mut data)?;
    let medication_id = data
        .get("id")
        .and_then(Value::as_str)
        .ok_or(RecordError::Internal)?
        .to_owned();
    let schedules = load_schedules(con, &medication_id)?;
    attach_assessment(con, &mut data, &medication_id)?;
    Ok(Medication { data, schedules })
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

fn normalize_medication(data: &mut Map<String, Value>) -> Result<(), RecordError> {
    for key in ["active", "as_needed", "long_term"] {
        let enabled = match data.get(key) {
            Some(Value::Bool(value)) => *value,
            Some(Value::Number(value)) => value.as_i64().unwrap_or(0) != 0,
            Some(Value::Null) | None => false,
            _ => return Err(RecordError::Internal),
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

fn load_schedules(con: &Connection, medication_id: &str) -> Result<Vec<Schedule>, RecordError> {
    let mut statement = con
        .prepare(
            "SELECT time_of_day,dose_text FROM medication_schedules
             WHERE medication_id=? ORDER BY time_of_day",
        )
        .map_err(|_| RecordError::Internal)?;
    let rows = statement
        .query_map([medication_id], |row| {
            Ok(Schedule {
                time_of_day: row.get(0)?,
                dose_text: row.get(1)?,
            })
        })
        .map_err(|_| RecordError::Internal)?;
    let mut keyed = rows
        .map(|row| {
            let schedule = row.map_err(|_| RecordError::Internal)?;
            Ok((clock_sort_key(&schedule.time_of_day)?, schedule))
        })
        .collect::<Result<Vec<_>, RecordError>>()?;
    keyed.sort_by_key(|(key, _)| *key);
    Ok(keyed.into_iter().map(|(_, schedule)| schedule).collect())
}

fn attach_assessment(
    con: &Connection,
    data: &mut Map<String, Value>,
    medication_id: &str,
) -> Result<(), RecordError> {
    let revision = data.get("revision").and_then(Value::as_i64).unwrap_or(1);
    let assessment = con
        .query_row(
            "SELECT assessment_json FROM medication_revisions WHERE medication_id=? AND revision=?",
            params![medication_id, revision],
            |row| row.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(|_| RecordError::Internal)?
        .flatten();
    if let Some(assessment) = assessment {
        let value = serde_json::from_str(&assessment).map_err(|_| RecordError::Internal)?;
        data.insert("assessment".to_owned(), value);
    }
    Ok(())
}

fn optional_date(value: Option<&str>) -> Result<Option<NaiveDate>, RecordError> {
    value.map(parse_date).transpose()
}

fn parse_date(value: &str) -> Result<NaiveDate, RecordError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|_| RecordError::BadRequest(format!("Invalid isoformat string: '{value}'")))
}
