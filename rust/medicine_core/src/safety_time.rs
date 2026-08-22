use chrono::{Datelike, FixedOffset, NaiveDate, TimeZone, Utc};
use serde_json::{Map, Value};

#[derive(Clone, Copy)]
pub(crate) struct Course {
    pub(crate) start: NaiveDate,
    pub(crate) end: Option<NaiveDate>,
    pub(crate) empty: bool,
}

pub(crate) fn age_years(birth_date: &str, as_of: Option<NaiveDate>) -> Result<i32, ()> {
    let birth = parse_date(birth_date)?;
    let today = as_of.unwrap_or_else(today_kst);
    let mut years = today.year() - birth.year();
    if today < anniversary(birth, birth.year() + years)? {
        years -= 1;
    }
    Ok(years.max(0))
}

pub(crate) fn courses_overlap(
    left: &Map<String, Value>,
    right: &Map<String, Value>,
) -> Option<bool> {
    let left = course(left)?;
    let right = course(right)?;
    if left.empty || right.empty {
        return Some(false);
    }
    if left.end.is_some_and(|end| end < right.start) {
        return Some(false);
    }
    if right.end.is_some_and(|end| end < left.start) {
        return Some(false);
    }
    Some(true)
}

pub(crate) fn parse_optional_date(value: Option<&str>) -> Result<Option<NaiveDate>, ()> {
    value.map(parse_date).transpose()
}

pub(crate) fn course(value: &Map<String, Value>) -> Option<Course> {
    let start = parse_date(value.get("start_date")?.as_str()?).ok()?;
    let mut end = value
        .get("end_date")
        .and_then(Value::as_str)
        .map(parse_date)
        .transpose()
        .ok()?;
    if end.is_some_and(|end| end < start) {
        return None;
    }
    let active = value.get("active").and_then(Value::as_bool).unwrap_or(true);
    let stopped = if !active {
        value
            .get("stopped_at")
            .and_then(Value::as_str)
            .map(parse_date)
            .transpose()
            .ok()?
    } else {
        None
    };
    if stopped.is_some_and(|stopped| stopped < start) {
        return Some(Course {
            start,
            end: stopped,
            empty: true,
        });
    }
    if let Some(stopped) = stopped {
        end = Some(end.map_or(stopped, |end| end.min(stopped)));
    }
    Some(Course {
        start,
        end,
        empty: false,
    })
}

fn anniversary(birth: NaiveDate, year: i32) -> Result<NaiveDate, ()> {
    if let Some(value) = birth.with_year(year) {
        return Ok(value);
    }
    NaiveDate::from_ymd_opt(year, 2, 28).ok_or(())
}

fn parse_date(value: &str) -> Result<NaiveDate, ()> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| ())
}

pub(crate) fn today_kst() -> NaiveDate {
    let offset = FixedOffset::east_opt(9 * 60 * 60).expect("valid KST offset");
    offset
        .from_utc_datetime(&Utc::now().naive_utc())
        .date_naive()
}
