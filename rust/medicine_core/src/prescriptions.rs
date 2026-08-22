use chrono::{Duration as ChronoDuration, FixedOffset, NaiveDate, Utc};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

const MEAL_RELATIONS: [&str; 6] = [
    "unspecified",
    "before_meal",
    "after_meal",
    "with_meal",
    "empty_stomach",
    "regardless",
];
const ADMINISTRATION_ROUTES: [&str; 9] = [
    "oral",
    "topical",
    "inhaled",
    "ophthalmic",
    "otic",
    "nasal",
    "injection",
    "other",
    "unknown",
];

pub(crate) enum DraftError {
    BadRequest(String),
    Internal,
}

pub(crate) fn inspect(
    body_json: &str,
    person_id: Option<&str>,
    product_ref: Option<&str>,
) -> (u16, Value) {
    let result = parse_body(body_json).and_then(|values| {
        let draft = normalize(&values)?;
        let hash = match (person_id, product_ref) {
            (Some(person_id), Some(product_ref)) => {
                Some(draft_hash(person_id, product_ref, &draft)?)
            }
            _ => None,
        };
        Ok(json!({"draft": draft, "draft_hash": hash}))
    });
    match result {
        Ok(body) => (200, body),
        Err(DraftError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(DraftError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

pub(crate) fn normalize(values: &Map<String, Value>) -> Result<Value, DraftError> {
    let schedule_times = normalize_schedule_times(values.get("schedule_times"))?;
    let as_needed = values.get("as_needed").is_some_and(python_truthy);

    let mut frequency = values
        .get("frequency_per_day")
        .filter(|value| !value.is_null());
    let inferred_frequency;
    if frequency.is_none() && !schedule_times.is_empty() {
        inferred_frequency = Value::from(schedule_times.len() as u64);
        frequency = Some(&inferred_frequency);
    }
    let frequency = positive_integer(frequency, "frequency_per_day", 24)?;
    if !schedule_times.is_empty() && frequency != Some(schedule_times.len() as u32) {
        return Err(DraftError::BadRequest(
            "frequency_per_day must match the number of schedule_times".to_owned(),
        ));
    }

    let prn_max = positive_integer(values.get("prn_max_per_day"), "prn_max_per_day", 24)?;
    if as_needed && (frequency.is_some() || !schedule_times.is_empty()) {
        return Err(DraftError::BadRequest(
            "PRN/as_needed medication cannot have a fixed daily frequency or schedule".to_owned(),
        ));
    }
    if !as_needed && prn_max.is_some() {
        return Err(DraftError::BadRequest(
            "prn_max_per_day is only valid for PRN/as_needed medication".to_owned(),
        ));
    }

    let amount = normalize_amount(values.get("dose_amount"))?;
    let dose_unit = optional_trimmed_string(values.get("dose_unit"), "dose_unit")?;

    let meal_relation = enum_value(values, "meal_relation", "unspecified", &MEAL_RELATIONS)?;
    let administration_route = enum_value(
        values,
        "administration_route",
        "unknown",
        &ADMINISTRATION_ROUTES,
    )?;

    let prescription_days =
        positive_integer(values.get("prescription_days"), "prescription_days", 3650)?;
    let long_term = values.get("long_term").is_some_and(python_truthy);
    let start = optional_date(values.get("start_date"))?.unwrap_or_else(today_kst);
    let mut finish = optional_date(values.get("end_date"))?;
    if long_term && (prescription_days.is_some() || finish.is_some()) {
        return Err(DraftError::BadRequest(
            "long_term medication cannot also have a prescription duration or end_date".to_owned(),
        ));
    }
    if let Some(days) = prescription_days {
        let computed = start
            .checked_add_signed(ChronoDuration::days(i64::from(days) - 1))
            .ok_or(DraftError::Internal)?;
        if finish.is_some_and(|value| value != computed) {
            return Err(DraftError::BadRequest(
                "end_date conflicts with start_date and prescription_days".to_owned(),
            ));
        }
        finish = Some(computed);
    }
    if finish.is_some_and(|value| value < start) {
        return Err(DraftError::BadRequest(
            "end_date must be on or after start_date".to_owned(),
        ));
    }

    let mut dosage_text = values.get("dosage_text").cloned().unwrap_or(Value::Null);
    if dosage_text.is_null() {
        if let Some(amount) = amount.as_deref() {
            dosage_text = Value::String(format!("{amount}{}", dose_unit.as_deref().unwrap_or("")));
        }
    }

    Ok(json!({
        "dosage_text": dosage_text,
        "dose_amount": amount,
        "dose_unit": dose_unit,
        "frequency_per_day": frequency,
        "meal_relation": meal_relation,
        "administration_route": administration_route,
        "as_needed": as_needed,
        "prn_max_per_day": prn_max,
        "prescription_days": prescription_days,
        "long_term": long_term,
        "schedule_times": schedule_times,
        "start_date": start.format("%Y-%m-%d").to_string(),
        "end_date": finish.map(|value| value.format("%Y-%m-%d").to_string()),
    }))
}

pub(crate) fn draft_hash(
    person_id: &str,
    product_ref: &str,
    draft: &Value,
) -> Result<String, DraftError> {
    draft_hash_optional(person_id, Some(product_ref), draft)
}

pub(crate) fn draft_hash_optional(
    person_id: &str,
    product_ref: Option<&str>,
    draft: &Value,
) -> Result<String, DraftError> {
    let Value::Object(draft) = draft else {
        return Err(DraftError::Internal);
    };
    let mut payload = Map::new();
    payload.insert("person_id".to_owned(), Value::String(person_id.to_owned()));
    payload.insert(
        "product_ref".to_owned(),
        product_ref
            .map(|value| Value::String(value.to_owned()))
            .unwrap_or(Value::Null),
    );
    for (key, value) in draft {
        payload.insert(key.clone(), value.clone());
    }
    let encoded =
        serde_json::to_string(&Value::Object(payload)).map_err(|_| DraftError::Internal)?;
    let digest = Sha256::digest(encoded.as_bytes());
    Ok(format!("{digest:x}"))
}

fn parse_body(body_json: &str) -> Result<Map<String, Value>, DraftError> {
    if body_json.trim().is_empty() {
        return Ok(Map::new());
    }
    let payload = serde_json::from_str::<Value>(body_json)
        .map_err(|_| DraftError::BadRequest("request body must be valid JSON".to_owned()))?;
    payload
        .as_object()
        .cloned()
        .ok_or_else(|| DraftError::BadRequest("request body must be a JSON object".to_owned()))
}

fn normalize_schedule_times(value: Option<&Value>) -> Result<Vec<String>, DraftError> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    if !python_truthy(value) {
        return Ok(Vec::new());
    }
    let values = value.as_array().ok_or_else(schedule_time_error)?;
    let mut result = Vec::with_capacity(values.len());
    for value in values {
        let raw = value.as_str().ok_or_else(schedule_time_error)?;
        result.push(normalize_time(raw)?);
    }
    let unique = result.iter().collect::<std::collections::BTreeSet<_>>();
    if unique.len() != result.len() {
        return Err(DraftError::BadRequest(
            "schedule_times must not contain duplicates".to_owned(),
        ));
    }
    Ok(result)
}

fn normalize_time(value: &str) -> Result<String, DraftError> {
    let Some((hour, minute)) = value.split_once(':') else {
        return Err(schedule_time_error());
    };
    if hour.is_empty()
        || hour.len() > 2
        || minute.is_empty()
        || minute.len() > 2
        || !hour.bytes().all(|byte| byte.is_ascii_digit())
        || !minute.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(schedule_time_error());
    }
    let hour = hour.parse::<u32>().map_err(|_| schedule_time_error())?;
    let minute = minute.parse::<u32>().map_err(|_| schedule_time_error())?;
    if hour > 23 || minute > 59 {
        return Err(schedule_time_error());
    }
    Ok(format!("{hour:02}:{minute:02}"))
}

fn schedule_time_error() -> DraftError {
    DraftError::BadRequest("schedule time must be HH:MM".to_owned())
}

fn positive_integer(
    value: Option<&Value>,
    name: &str,
    maximum: u32,
) -> Result<Option<u32>, DraftError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let raw = decimal_source(value)
        .ok_or_else(|| DraftError::BadRequest(format!("{name} must be a positive integer")))?;
    let normalized = normalize_decimal(&raw)
        .ok_or_else(|| DraftError::BadRequest(format!("{name} must be a positive integer")))?;
    if normalized.starts_with('-') || normalized == "0" || normalized.contains('.') {
        return Err(DraftError::BadRequest(format!(
            "{name} must be between 1 and {maximum}"
        )));
    }
    let parsed = normalized
        .parse::<u128>()
        .map_err(|_| DraftError::BadRequest(format!("{name} must be between 1 and {maximum}")))?;
    if parsed == 0 || parsed > u128::from(maximum) {
        return Err(DraftError::BadRequest(format!(
            "{name} must be between 1 and {maximum}"
        )));
    }
    Ok(Some(parsed as u32))
}

fn normalize_amount(value: Option<&Value>) -> Result<Option<String>, DraftError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let raw = decimal_source(value)
        .and_then(|value| normalize_decimal(&value))
        .ok_or_else(|| DraftError::BadRequest("dose_amount must be a finite number".to_owned()))?;
    if raw.starts_with('-') || raw == "0" {
        return Err(DraftError::BadRequest("dose_amount must be > 0".to_owned()));
    }
    Ok(Some(raw))
}

fn decimal_source(value: &Value) -> Option<String> {
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        Value::Bool(true) => Some("True".to_owned()),
        Value::Bool(false) => Some("False".to_owned()),
        _ => None,
    }
}

fn normalize_decimal(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }
    let (negative, unsigned) = match raw.as_bytes()[0] {
        b'-' => (true, &raw[1..]),
        b'+' => (false, &raw[1..]),
        _ => (false, raw),
    };
    if unsigned.is_empty() {
        return None;
    }
    let mut exponent_split = unsigned.split(['e', 'E']);
    let mantissa = exponent_split.next()?;
    let exponent_text = exponent_split.next();
    if exponent_split.next().is_some() {
        return None;
    }
    let exponent = match exponent_text {
        None => 0_i32,
        Some(value) => value.parse::<i32>().ok()?,
    };
    if exponent.unsigned_abs() > 10_000 {
        return None;
    }
    let mut decimal_split = mantissa.split('.');
    let integer = decimal_split.next()?;
    let fraction = decimal_split.next().unwrap_or("");
    if decimal_split.next().is_some()
        || (integer.is_empty() && fraction.is_empty())
        || !integer.bytes().all(|byte| byte.is_ascii_digit())
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let mut digits = format!("{integer}{fraction}");
    if digits.is_empty() {
        return None;
    }
    let first_nonzero = digits.find(|value: char| value != '0');
    let Some(first_nonzero) = first_nonzero else {
        return Some("0".to_owned());
    };
    digits = digits[first_nonzero..].to_owned();
    let scale = i32::try_from(fraction.len()).ok()?.checked_sub(exponent)?;
    let mut plain = if scale <= 0 {
        let zeros = usize::try_from(-scale).ok()?;
        format!("{digits}{}", "0".repeat(zeros))
    } else {
        let scale = usize::try_from(scale).ok()?;
        if digits.len() > scale {
            let split = digits.len() - scale;
            format!("{}.{}", &digits[..split], &digits[split..])
        } else {
            format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
        }
    };
    if plain.contains('.') {
        while plain.ends_with('0') {
            plain.pop();
        }
        if plain.ends_with('.') {
            plain.pop();
        }
    }
    if negative && plain != "0" {
        plain.insert(0, '-');
    }
    Some(plain)
}

fn optional_trimmed_string(
    value: Option<&Value>,
    name: &str,
) -> Result<Option<String>, DraftError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if !python_truthy(value) {
        return Ok(None);
    }
    let value = value
        .as_str()
        .ok_or_else(|| DraftError::BadRequest(format!("{name} must be a string")))?
        .trim();
    Ok((!value.is_empty()).then(|| value.to_owned()))
}

fn enum_value(
    values: &Map<String, Value>,
    name: &str,
    default: &str,
    allowed: &[&str],
) -> Result<String, DraftError> {
    let Some(value) = values.get(name) else {
        return Ok(default.to_owned());
    };
    let text = value.as_str().ok_or_else(|| {
        DraftError::BadRequest(format!("invalid {name}: {}", python_display(value)))
    })?;
    if !allowed.contains(&text) {
        return Err(DraftError::BadRequest(format!("invalid {name}: {text}")));
    }
    Ok(text.to_owned())
}

fn optional_date(value: Option<&Value>) -> Result<Option<NaiveDate>, DraftError> {
    let Some(value) = value else {
        return Ok(None);
    };
    if !python_truthy(value) {
        return Ok(None);
    }
    let raw = value
        .as_str()
        .ok_or_else(|| DraftError::BadRequest("date value must be an ISO date".to_owned()))?;
    NaiveDate::parse_from_str(raw, "%Y-%m-%d")
        .map(Some)
        .map_err(|_| DraftError::BadRequest(format!("Invalid isoformat string: '{raw}'")))
}

fn today_kst() -> NaiveDate {
    let korea = FixedOffset::east_opt(9 * 60 * 60).expect("KST offset is valid");
    Utc::now().with_timezone(&korea).date_naive()
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

fn python_display(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::String(value) => value.clone(),
        other => other.to_string(),
    }
}
