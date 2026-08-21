use chrono::{Datelike, FixedOffset, NaiveDate, Utc};
use rusqlite::{params, Connection, Row, TransactionBehavior};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;
use uuid::Uuid;

use crate::personal_db::{self, Access, OpenError};

const PERSON_FIELDS: [&str; 6] = [
    "name",
    "birth_date",
    "sex",
    "pregnancy_status",
    "lactation_status",
    "notes",
];

enum PeopleRoute<'a> {
    List,
    Create,
    Update(&'a str),
    Delete(&'a str),
}

enum PeopleError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

struct PersonRecord {
    id: String,
    name: String,
    birth_date: String,
    sex: String,
    pregnancy_status: String,
    lactation_status: String,
    notes: Option<String>,
    created_at: String,
}

impl PersonRecord {
    fn from_row(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Self {
            id: row.get("id")?,
            name: row.get("name")?,
            birth_date: row.get("birth_date")?,
            sex: row.get("sex")?,
            pregnancy_status: row.get("pregnancy_status")?,
            lactation_status: row.get("lactation_status")?,
            notes: row.get("notes")?,
            created_at: row.get("created_at")?,
        })
    }

    fn to_json(&self) -> Result<Value, PeopleError> {
        Ok(json!({
            "id": self.id,
            "name": self.name,
            "birth_date": self.birth_date,
            "sex": self.sex,
            "pregnancy_status": self.pregnancy_status,
            "lactation_status": self.lactation_status,
            "notes": self.notes,
            "created_at": self.created_at,
            "age": age_years(&self.birth_date)?,
            "profile_needs_review": profile_needs_review(self),
        }))
    }
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
        PeopleRoute::List => list_people(personal_db),
        PeopleRoute::Create => create_person(personal_db, body_json),
        PeopleRoute::Update(person_id) => update_person(personal_db, person_id, body_json),
        PeopleRoute::Delete(person_id) => delete_person(personal_db, person_id),
    };
    Some(match result {
        Ok(response) => response,
        Err(PeopleError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(PeopleError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(PeopleError::Unavailable) => (503, json!({"detail": "personal database unavailable"})),
        Err(PeopleError::Internal) => (500, json!({"detail": "unexpected server error"})),
    })
}

fn route<'a>(method: &str, path: &'a str) -> Option<PeopleRoute<'a>> {
    let method = method.trim().to_ascii_uppercase();
    if path == "/api/people" {
        return match method.as_str() {
            "GET" => Some(PeopleRoute::List),
            "POST" => Some(PeopleRoute::Create),
            _ => None,
        };
    }
    let person_id = path.strip_prefix("/api/people/")?;
    if person_id.is_empty() || person_id.contains('/') {
        return None;
    }
    match method.as_str() {
        "PATCH" => Some(PeopleRoute::Update(person_id)),
        "DELETE" => Some(PeopleRoute::Delete(person_id)),
        _ => None,
    }
}

fn list_people(personal_db: Option<&Path>) -> Result<(u16, Value), PeopleError> {
    let con = open_personal(personal_db, true)?;
    let mut statement = con
        .prepare(
            "SELECT id,name,birth_date,sex,pregnancy_status,lactation_status,notes,created_at
             FROM people ORDER BY rowid",
        )
        .map_err(|_| PeopleError::Internal)?;
    let people = statement
        .query_map([], PersonRecord::from_row)
        .map_err(|_| PeopleError::Internal)?
        .map(|row| {
            row.map_err(|_| PeopleError::Internal)
                .and_then(|person| person.to_json())
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok((200, Value::Array(people)))
}

fn create_person(personal_db: Option<&Path>, body_json: &str) -> Result<(u16, Value), PeopleError> {
    let payload = validated_payload(body_json)?;
    let name = required_string(&payload, "name")?.trim().to_owned();
    if name.is_empty() {
        return Err(PeopleError::BadRequest("name is required".to_owned()));
    }
    let birth_date = required_string(&payload, "birth_date")?.to_owned();
    parse_birth_date(&birth_date)?;
    let sex = optional_string(&payload, "sex")?.unwrap_or("unknown");
    let pregnancy_status = optional_string(&payload, "pregnancy_status")?.unwrap_or("unknown");
    let lactation_status = optional_string(&payload, "lactation_status")?.unwrap_or("unknown");
    let (pregnancy_status, lactation_status) =
        normalize_reproductive_status(sex, pregnancy_status, lactation_status)?;
    let notes = nullable_string(&payload, "notes")?;

    let mut con = open_personal(personal_db, false)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| PeopleError::Internal)?;
    let person_id = Uuid::new_v4().to_string();
    transaction
        .execute(
            "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,lactation_status,notes)
             VALUES(?,?,?,?,?,?,?)",
            params![
                person_id,
                name,
                birth_date,
                sex,
                pregnancy_status,
                lactation_status,
                notes
            ],
        )
        .map_err(|_| PeopleError::Internal)?;
    let person = query_person(&transaction, &person_id)?;
    transaction.commit().map_err(|_| PeopleError::Internal)?;
    Ok((201, person.to_json()?))
}

fn update_person(
    personal_db: Option<&Path>,
    person_id: &str,
    body_json: &str,
) -> Result<(u16, Value), PeopleError> {
    let payload = validated_payload(body_json)?;
    let name = required_string(&payload, "name")?.trim().to_owned();
    if name.is_empty() {
        return Err(PeopleError::BadRequest("name is required".to_owned()));
    }
    let birth_date = required_string(&payload, "birth_date")?.to_owned();
    parse_birth_date(&birth_date)?;
    let sex = required_string(&payload, "sex")?;
    let pregnancy_status = required_string(&payload, "pregnancy_status")?;
    let lactation_status = required_string(&payload, "lactation_status")?;
    let (pregnancy_status, lactation_status) =
        normalize_reproductive_status(sex, pregnancy_status, lactation_status)?;
    let notes = nullable_string(&payload, "notes")?;

    let mut con = open_personal(personal_db, false)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| PeopleError::Internal)?;
    if !person_exists(&transaction, person_id)? {
        return Err(PeopleError::NotFound("person not found".to_owned()));
    }
    transaction
        .execute(
            "UPDATE people
             SET name=?,birth_date=?,sex=?,pregnancy_status=?,lactation_status=?,notes=?
             WHERE id=?",
            params![
                name,
                birth_date,
                sex,
                pregnancy_status,
                lactation_status,
                notes,
                person_id
            ],
        )
        .map_err(|_| PeopleError::Internal)?;
    let person = query_person(&transaction, person_id)?;
    transaction.commit().map_err(|_| PeopleError::Internal)?;
    Ok((200, person.to_json()?))
}

fn delete_person(personal_db: Option<&Path>, person_id: &str) -> Result<(u16, Value), PeopleError> {
    let mut con = open_personal(personal_db, false)?;
    let transaction = con
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| PeopleError::Internal)?;
    if !person_exists(&transaction, person_id)? {
        return Err(PeopleError::NotFound("person not found".to_owned()));
    }
    transaction
        .execute("DELETE FROM dose_logs WHERE person_id=?", [person_id])
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute("DELETE FROM dose_instances WHERE person_id=?", [person_id])
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute(
            "DELETE FROM medication_requests WHERE person_id=?",
            [person_id],
        )
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute(
            "DELETE FROM medication_schedules WHERE medication_id IN
             (SELECT id FROM medications WHERE person_id=?)",
            [person_id],
        )
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute(
            "DELETE FROM medication_revisions WHERE medication_id IN
             (SELECT id FROM medications WHERE person_id=?)",
            [person_id],
        )
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute("DELETE FROM medications WHERE person_id=?", [person_id])
        .map_err(|_| PeopleError::Internal)?;
    transaction
        .execute("DELETE FROM people WHERE id=?", [person_id])
        .map_err(|_| PeopleError::Internal)?;
    transaction.commit().map_err(|_| PeopleError::Internal)?;
    Ok((200, json!({"id": person_id, "deleted": true})))
}

fn open_personal(personal_db: Option<&Path>, read_only: bool) -> Result<Connection, PeopleError> {
    personal_db::open(
        personal_db,
        if read_only {
            Access::ReadOnly
        } else {
            Access::ReadWrite
        },
    )
    .map_err(|error| match error {
        OpenError::Unavailable => PeopleError::Unavailable,
        OpenError::Sql => PeopleError::Internal,
    })
}

fn validated_payload(body_json: &str) -> Result<Map<String, Value>, PeopleError> {
    let payload = if body_json.trim().is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_str(body_json)
            .map_err(|_| PeopleError::BadRequest("request body must be valid JSON".to_owned()))?
    };
    let object = payload
        .as_object()
        .ok_or_else(|| PeopleError::BadRequest("request body must be a JSON object".to_owned()))?;
    let allowed: BTreeSet<&str> = PERSON_FIELDS.into_iter().collect();
    let unknown = object
        .keys()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect::<Vec<_>>();
    if !unknown.is_empty() {
        return Err(PeopleError::BadRequest(format!(
            "unknown fields: {}",
            unknown.join(", ")
        )));
    }
    Ok(object.clone())
}

fn required_string<'a>(payload: &'a Map<String, Value>, key: &str) -> Result<&'a str, PeopleError> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| PeopleError::BadRequest(format!("{key} is required")))
}

fn optional_string<'a>(
    payload: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, PeopleError> {
    match payload.get(key) {
        None => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        _ => Err(PeopleError::BadRequest(format!("{key} must be a string"))),
    }
}

fn nullable_string(payload: &Map<String, Value>, key: &str) -> Result<Option<String>, PeopleError> {
    match payload.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        _ => Err(PeopleError::BadRequest(format!(
            "{key} must be a string or null"
        ))),
    }
}

fn normalize_reproductive_status<'a>(
    sex: &'a str,
    pregnancy_status: &'a str,
    lactation_status: &'a str,
) -> Result<(&'a str, &'a str), PeopleError> {
    if !matches!(sex, "female" | "male") {
        return Err(PeopleError::BadRequest(
            "sex must be male or female".to_owned(),
        ));
    }
    if sex == "male" {
        return Ok(("not_applicable", "not_applicable"));
    }
    if !matches!(pregnancy_status, "pregnant" | "not_pregnant") {
        return Err(PeopleError::BadRequest(
            "pregnancy_status must be pregnant or not_pregnant for female profiles".to_owned(),
        ));
    }
    if !matches!(lactation_status, "breastfeeding" | "not_breastfeeding") {
        return Err(PeopleError::BadRequest(
            "lactation_status must be breastfeeding or not_breastfeeding for female profiles"
                .to_owned(),
        ));
    }
    Ok((pregnancy_status, lactation_status))
}

fn parse_birth_date(value: &str) -> Result<NaiveDate, PeopleError> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|_| PeopleError::BadRequest("birth_date must be YYYY-MM-DD".to_owned()))
}

fn age_years(birth_date: &str) -> Result<i32, PeopleError> {
    let birth = parse_birth_date(birth_date)?;
    let korea = FixedOffset::east_opt(9 * 60 * 60).ok_or(PeopleError::Internal)?;
    let today = Utc::now().with_timezone(&korea).date_naive();
    let mut years = today.year() - birth.year();
    let anniversary = birth.with_year(today.year()).unwrap_or_else(|| {
        NaiveDate::from_ymd_opt(today.year(), 2, 28).expect("February 28 is always valid")
    });
    if today < anniversary {
        years -= 1;
    }
    Ok(years.max(0))
}

fn profile_needs_review(person: &PersonRecord) -> bool {
    if !matches!(person.sex.as_str(), "female" | "male") {
        return true;
    }
    person.sex == "female"
        && (!matches!(
            person.pregnancy_status.as_str(),
            "pregnant" | "not_pregnant"
        ) || !matches!(
            person.lactation_status.as_str(),
            "breastfeeding" | "not_breastfeeding"
        ))
}

fn person_exists(con: &Connection, person_id: &str) -> Result<bool, PeopleError> {
    con.query_row(
        "SELECT EXISTS(SELECT 1 FROM people WHERE id=?)",
        [person_id],
        |row| row.get::<_, i64>(0),
    )
    .map(|exists| exists != 0)
    .map_err(|_| PeopleError::Internal)
}

fn query_person(con: &Connection, person_id: &str) -> Result<PersonRecord, PeopleError> {
    con.query_row(
        "SELECT id,name,birth_date,sex,pregnancy_status,lactation_status,notes,created_at
         FROM people WHERE id=?",
        [person_id],
        PersonRecord::from_row,
    )
    .map_err(|_| PeopleError::Internal)
}
