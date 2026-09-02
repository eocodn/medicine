use chrono::NaiveDate;
use regex::Regex;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::OnceLock;

use crate::canonical_products::{self, ProductError};
use crate::profile_age::evaluate_age_rule;
use crate::reference_queries::{linked_product_rows, resolved_product_rows};
use crate::reference_semantics::{
    criterion_note_requires_review, dedupe_qualifiers, has_semantics, qualifiers,
};
use crate::safety_time::age_years;

#[derive(Debug)]
enum ProfileError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

pub(crate) fn inspect(
    canonical_db: Option<&Path>,
    product_ref: &str,
    person_json: &str,
    candidate_course_json: &str,
    as_of: Option<&str>,
) -> (u16, Value) {
    match inspect_inner(
        canonical_db,
        product_ref,
        person_json,
        candidate_course_json,
        as_of,
    ) {
        Ok(body) => (200, body),
        Err(ProfileError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(ProfileError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(ProfileError::Unavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(ProfileError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

fn inspect_inner(
    canonical_db: Option<&Path>,
    product_ref: &str,
    person_json: &str,
    candidate_course_json: &str,
    as_of: Option<&str>,
) -> Result<Value, ProfileError> {
    let con = canonical_products::open(canonical_db).map_err(ProfileError::from)?;
    let product = canonical_products::resolve_from_connection(&con, product_ref)
        .map_err(ProfileError::from)?;
    let person = parse_object(person_json, "person")?;
    let candidate_course = parse_object(candidate_course_json, "candidate_course")?;
    let as_of = parse_as_of(as_of)?;
    let risks = evaluate(&con, &product, &person, as_of, &candidate_course)
        .map_err(|_| ProfileError::Internal)?;
    Ok(json!({"product": product, "risks": risks}))
}

pub(crate) fn evaluate(
    con: &Connection,
    product: &Value,
    person: &Map<String, Value>,
    as_of: Option<NaiveDate>,
    candidate_course: &Map<String, Value>,
) -> Result<Vec<Value>, ()> {
    let product = product.as_object().ok_or(())?;
    let target = item_seq(product).ok_or(())?;
    let mut rows = Vec::new();
    for category in ["age_contraindication", "pregnancy_contraindication"] {
        rows.extend(linked_product_rows(con, target, category)?);
    }
    rows.extend(resolved_product_rows(con, target, "elderly_caution")?);

    let evaluation_dates = profile_evaluation_dates(candidate_course, as_of);
    let pregnancy_rows = rows
        .iter()
        .filter(|row| text(row, "category") == Some("pregnancy_contraindication"))
        .collect::<Vec<_>>();
    let pregnancy_grades = pregnancy_rows
        .iter()
        .filter_map(|row| pregnancy_grade(text(row, "criterion_rule_value")))
        .collect::<BTreeSet<_>>();
    let conflicting_pregnancy_grades =
        text(person, "pregnancy_status") == Some("pregnant") && pregnancy_grades.len() > 1;

    let mut risks = Vec::new();
    if conflicting_pregnancy_grades {
        let first = pregnancy_rows.first().ok_or(())?;
        let mut finding = json!({
            "type": "pregnancy_contraindication",
            "severity": "info",
            "title": "임부금기 기준 확인 필요",
            "details": "서로 다른 임부금기 등급이 함께 적용되어 자동 판정하지 않습니다. 의사 또는 약사에게 확인하세요.",
            "evaluation_status": "unknown",
            "source_scope": "canonical_product",
            "dataset_key": first.get("criterion_source_dataset_key").cloned().unwrap_or(Value::Null),
            "source_row": first.get("criterion_source_row").cloned().unwrap_or(Value::Null),
        });
        let mut qualifier_values = Vec::new();
        for row in &pregnancy_rows {
            qualifier_values.extend(qualifiers(con, row)?);
        }
        let qualifier_values = dedupe_qualifiers(qualifier_values);
        if !qualifier_values.is_empty() {
            finding["qualifiers"] = Value::Array(qualifier_values);
        }
        risks.push(finding);
    }

    let mut unanimous_review_categories = BTreeSet::new();
    for row in &rows {
        if text(row, "match_method") == Some("mfds_unanimous_value") && has_semantics(con, row)? {
            if let Some(category) = text(row, "category") {
                unanimous_review_categories.insert(category.to_owned());
            }
        }
    }

    for row in &rows {
        let category = text(row, "category").ok_or(())?;
        let rule_value = text(row, "criterion_rule_value");
        let (title, severity) = match category {
            "age_contraindication" => {
                let birth_date = text(person, "birth_date").ok_or(())?;
                let product_form =
                    text(row, "product_dosage_form").or_else(|| text(product, "dosage_form"));
                let evaluations = evaluation_dates
                    .iter()
                    .map(|date| evaluate_age_rule(birth_date, rule_value, product_form, *date))
                    .collect::<Vec<_>>();
                if evaluations
                    .iter()
                    .any(|(applies, _)| *applies == Some(true))
                {
                    (
                        format!("연령금기 · {}", rule_value.unwrap_or("")),
                        "danger".to_owned(),
                    )
                } else if evaluations.iter().any(|(applies, _)| applies.is_none()) {
                    let reason = evaluations
                        .iter()
                        .find_map(|(applies, reason)| {
                            if applies.is_none() {
                                reason.as_ref()
                            } else {
                                None
                            }
                        })
                        .cloned()
                        .unwrap_or_else(|| "연령금기 기준을 자동 판정하지 못했습니다.".to_owned());
                    let mut finding = base_finding(
                        row,
                        category,
                        "info",
                        "연령금기 기준 확인 필요".to_owned(),
                        Some(reason),
                    );
                    finding["evaluation_status"] = Value::String("unknown".to_owned());
                    let qualifier_values = qualifiers(con, row)?;
                    if !qualifier_values.is_empty() {
                        finding["qualifiers"] = Value::Array(qualifier_values);
                    }
                    risks.push(finding);
                    continue;
                } else {
                    continue;
                }
            }
            "pregnancy_contraindication" => {
                if text(person, "pregnancy_status") != Some("pregnant")
                    || conflicting_pregnancy_grades
                {
                    continue;
                }
                (
                    format!("임부금기 · {}", pregnancy_rule_display(rule_value)),
                    "danger".to_owned(),
                )
            }
            "elderly_caution" => {
                let birth_date = text(person, "birth_date").ok_or(())?;
                if !evaluation_dates
                    .iter()
                    .any(|date| age_years(birth_date, *date).is_ok_and(|years| years >= 65))
                {
                    continue;
                }
                ("노인주의 대상".to_owned(), "warning".to_owned())
            }
            _ => continue,
        };

        let details = canonical_details(row);
        let mut finding = base_finding(row, category, &severity, title, details);
        let qualifier_values = qualifiers(con, row)?;
        if !qualifier_values.is_empty() {
            finding["qualifiers"] = Value::Array(qualifier_values);
        }
        let note_review = criterion_note_requires_review(con, row)?
            || (text(row, "match_method") == Some("mfds_unanimous_value")
                && unanimous_review_categories.contains(category));
        if note_review {
            finding["evaluation_status"] = Value::String("conditional".to_owned());
            finding["details"] = Value::String(details_with_professional_review(
                finding.get("details").and_then(Value::as_str),
            ));
        } else if category == "pregnancy_contraindication"
            && pregnancy_rule_is_conditional(rule_value)
        {
            finding["evaluation_status"] = Value::String("conditional".to_owned());
        }
        risks.push(finding);
    }
    Ok(risks)
}

impl From<ProductError> for ProfileError {
    fn from(value: ProductError) -> Self {
        match value {
            ProductError::BadRequest(detail) => Self::BadRequest(detail),
            ProductError::NotFound(detail) => Self::NotFound(detail),
            ProductError::Unavailable => Self::Unavailable,
            ProductError::Internal => Self::Internal,
        }
    }
}

fn parse_object(raw: &str, name: &str) -> Result<Map<String, Value>, ProfileError> {
    let value = serde_json::from_str::<Value>(raw)
        .map_err(|_| ProfileError::BadRequest(format!("{name} must be valid JSON")))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| ProfileError::BadRequest(format!("{name} must be a JSON object")))
}

fn parse_as_of(raw: Option<&str>) -> Result<Option<NaiveDate>, ProfileError> {
    raw.map(|value| {
        NaiveDate::parse_from_str(value, "%Y-%m-%d")
            .map_err(|_| ProfileError::BadRequest("as_of must be YYYY-MM-DD".to_owned()))
    })
    .transpose()
}

fn profile_evaluation_dates(
    candidate_course: &Map<String, Value>,
    as_of: Option<NaiveDate>,
) -> Vec<Option<NaiveDate>> {
    let (start, end) = parse_course_dates(candidate_course).unwrap_or((None, None));
    let first = match as_of {
        Some(as_of) if start.is_some_and(|start| as_of < start) => start,
        Some(as_of) => Some(as_of),
        None => start,
    };
    let mut dates = vec![first];
    if end.is_some_and(|end| first.is_none_or(|first| end > first)) {
        dates.push(end);
    }
    dates
}

fn parse_course_dates(
    candidate_course: &Map<String, Value>,
) -> Result<(Option<NaiveDate>, Option<NaiveDate>), ()> {
    let start = optional_date(candidate_course.get("start_date"))?;
    let end = optional_date(candidate_course.get("end_date"))?;
    Ok((start, end))
}

fn optional_date(value: Option<&Value>) -> Result<Option<NaiveDate>, ()> {
    let Some(value) = value else {
        return Ok(None);
    };
    if value.is_null() || value.as_str() == Some("") {
        return Ok(None);
    }
    let raw = value.as_str().ok_or(())?;
    NaiveDate::parse_from_str(raw, "%Y-%m-%d")
        .map(Some)
        .map_err(|_| ())
}

fn base_finding(
    row: &Map<String, Value>,
    category: &str,
    severity: &str,
    title: String,
    details: Option<String>,
) -> Value {
    json!({
        "type": category,
        "severity": severity,
        "title": title,
        "details": details,
        "source_scope": "canonical_product",
        "dataset_key": row.get("criterion_source_dataset_key").cloned().unwrap_or(Value::Null),
        "source_row": row.get("criterion_source_row").cloned().unwrap_or(Value::Null),
    })
}

fn canonical_details(row: &Map<String, Value>) -> Option<String> {
    nonempty_text(row, "product_details")
        .or_else(|| nonempty_text(row, "criterion_details"))
        .map(str::to_owned)
}

fn details_with_professional_review(details: Option<&str>) -> String {
    let advice = "세부 적용 조건이 있어 의사 또는 약사에게 확인하세요.";
    details
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map_or_else(|| advice.to_owned(), |value| format!("{value} {advice}"))
}

fn pregnancy_rule_display(value: Option<&str>) -> String {
    let text = value.unwrap_or("").trim();
    match text {
        "1" | "2" => format!("{text}등급"),
        "" => "등급 미표기".to_owned(),
        _ => text.to_owned(),
    }
}

fn pregnancy_grade(value: Option<&str>) -> Option<String> {
    pregnancy_grade_regex()
        .captures(value.unwrap_or(""))
        .and_then(|captures| captures.get(1))
        .map(|value| value.as_str().to_owned())
}

fn pregnancy_rule_is_conditional(value: Option<&str>) -> bool {
    pregnancy_conditional_regex().is_match(value.unwrap_or("").trim())
}

fn item_seq(product: &Map<String, Value>) -> Option<&str> {
    ["catalog_item_seq", "product_ref", "product_code"]
        .iter()
        .find_map(|key| nonempty_text(product, key))
}

fn nonempty_text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    text(row, key)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}

fn pregnancy_grade_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"([12])\s*등급").expect("valid pregnancy grade regex"))
}

fn pregnancy_conditional_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX
        .get_or_init(|| Regex::new(r"[12]\s*등급\s*\(").expect("valid conditional pregnancy regex"))
}
