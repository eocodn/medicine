use regex::Regex;
use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::sync::OnceLock;
use unicode_normalization::UnicodeNormalization;

use crate::reference_semantics::semantic_records;
use crate::safety_time::{course, courses_overlap, Course};

const SUPPORTED_RUNTIME_EVALUATORS: [&str; 2] = ["minimum_separation", "excluded_route"];
const POST_COURSE_MARKERS: [&str; 5] = [
    "종료 후",
    "중단 후",
    "중단한 직후",
    "중단 직후",
    "투여 중 및 종료 후",
];

pub(crate) fn remark_timing(
    con: &Connection,
    row: &Map<String, Value>,
    details: Option<&str>,
) -> Result<Value, ()> {
    let semantics = semantic_records(con, row)?;
    if let Some(semantic) = semantics
        .iter()
        .find(|semantic| text(semantic, "evaluator_kind") == Some("minimum_separation"))
    {
        let payload = semantic
            .get("structured_payload")
            .and_then(Value::as_object);
        let hours = payload
            .and_then(|payload| payload.get("hours"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        return Ok(json!({
            "status": "structured",
            "kind": "minimum_separation",
            "hours": hours,
            "amount": hours,
            "unit": "시간",
            "direction": payload
                .and_then(|payload| payload.get("direction"))
                .and_then(Value::as_str)
                .unwrap_or("symmetric"),
            "source_text": semantic.get("source_remark").cloned().unwrap_or(Value::Null),
        }));
    }

    if let Some(semantic) = semantics.iter().find(|semantic| {
        text(semantic, "semantic_role") == Some("applicability_condition")
            && text(semantic, "evaluation_mode") == Some("runtime_evaluable")
            && !SUPPORTED_RUNTIME_EVALUATORS
                .contains(&text(semantic, "evaluator_kind").unwrap_or(""))
            && text(semantic, "fallback_action") == Some("review_required")
    }) {
        return Ok(json!({
            "status": "not_evaluable",
            "kind": "unknown_contract_evaluator",
            "reason": "reference condition evaluator is not supported by this app version",
            "source_text": semantic.get("source_remark").cloned().unwrap_or(Value::Null),
        }));
    }

    if let Some(semantic) = semantics.iter().find(|semantic| {
        text(semantic, "semantic_role") == Some("applicability_condition")
            && text(semantic, "evaluation_mode") == Some("review_required")
            && text(semantic, "fallback_action") == Some("review_required")
    }) {
        return Ok(json!({
            "status": "not_evaluable",
            "kind": text(semantic, "runtime_error_kind").unwrap_or("review_required_condition"),
            "reason": "reference condition requires professional review",
            "source_text": semantic.get("source_remark").cloned().unwrap_or(Value::Null),
        }));
    }
    Ok(parse_timing(
        details.unwrap_or(""),
        canonical_ingredient(row),
        canonical_paired_ingredient(row),
    ))
}

pub(crate) fn interaction_timing_applies(
    timing: &Map<String, Value>,
    candidate_course: &Map<String, Value>,
    current_course: &Map<String, Value>,
    candidate_side: &str,
) -> bool {
    let candidate = course(candidate_course);
    let current = course(current_course);
    if candidate.is_some_and(|value| value.empty) || current.is_some_and(|value| value.empty) {
        return false;
    }
    let overlap = courses_overlap(candidate_course, current_course);
    if overlap.is_none() || overlap == Some(true) {
        return true;
    }
    if text(timing, "status") == Some("not_evaluable") {
        return true;
    }
    match text(timing, "kind") {
        Some("course_overlap") => false,
        Some("minimum_separation") => match (candidate, current) {
            (Some(candidate), Some(current)) => {
                potential_gap_within_hours(candidate, current, integer(timing, "hours"))
            }
            _ => true,
        },
        Some("washout_after") => match (candidate, current) {
            (Some(candidate), Some(current)) => washout_applies(
                timing,
                candidate,
                current,
                candidate_side,
                integer(timing, "hours"),
            ),
            _ => true,
        },
        _ => false,
    }
}

fn parse_timing(
    details: &str,
    left_ingredient: Option<&str>,
    right_ingredient: Option<&str>,
) -> Value {
    let source_text = details.split_whitespace().collect::<Vec<_>>().join(" ");
    if let Some(captures) = within_hours_regex().captures(&source_text) {
        let amount = captures
            .name("hours")
            .and_then(|value| value.as_str().parse::<i64>().ok())
            .unwrap_or(0);
        return json!({
            "status": "structured",
            "kind": "minimum_separation",
            "hours": amount,
            "amount": amount,
            "unit": "시간",
            "direction": "symmetric",
            "source_text": source_text,
        });
    }
    if let Some(captures) = after_course_regex().captures(&source_text) {
        let amount = captures
            .name("amount")
            .and_then(|value| value.as_str().parse::<i64>().ok())
            .unwrap_or(0);
        let unit = captures.name("unit").map_or("", |value| value.as_str());
        let hours = amount
            * match unit {
                "일" => 24,
                "주" => 7 * 24,
                _ => 1,
            };
        let subject = captures
            .name("subject")
            .map_or("", |value| value.as_str())
            .trim();
        if let Some(side) = source_side(subject, left_ingredient, right_ingredient) {
            return json!({
                "status": "structured",
                "kind": "washout_after",
                "hours": hours,
                "amount": amount,
                "unit": unit,
                "source_side": side,
                "subject": subject,
                "source_text": source_text,
            });
        }
        return json!({
            "status": "not_evaluable",
            "kind": "post_course_restriction",
            "reason": "washout source ingredient could not be resolved uniquely",
            "source_text": source_text,
        });
    }
    if POST_COURSE_MARKERS
        .iter()
        .any(|marker| source_text.contains(marker))
    {
        return json!({
            "status": "not_evaluable",
            "kind": "post_course_restriction",
            "reason": "post-course restriction duration could not be resolved",
            "source_text": source_text,
        });
    }
    json!({
        "status": "default",
        "kind": "course_overlap",
        "source_text": source_text,
    })
}

fn washout_applies(
    timing: &Map<String, Value>,
    candidate: Course,
    current: Course,
    candidate_side: &str,
    hours: i64,
) -> bool {
    let (left, right) = if candidate_side == "left" {
        (candidate, current)
    } else {
        (current, candidate)
    };
    let (source, target) = if text(timing, "source_side") == Some("left") {
        (left, right)
    } else {
        (right, left)
    };
    if source.end.is_none() {
        return target
            .end
            .is_none_or(|target_end| target_end >= source.start);
    }
    if target.start > source.end.expect("checked above") {
        return potential_gap_within_hours(source, target, hours);
    }
    false
}

fn potential_gap_within_hours(first: Course, second: Course, hours: i64) -> bool {
    if let Some(first_end) = first.end {
        if first_end < second.start {
            let calendar_days = (second.start - first_end).num_days();
            return (calendar_days - 1).max(0) * 24 < hours;
        }
    }
    if let Some(second_end) = second.end {
        if second_end < first.start {
            let calendar_days = (first.start - second_end).num_days();
            return (calendar_days - 1).max(0) * 24 < hours;
        }
    }
    true
}

fn source_side(
    subject: &str,
    left_ingredient: Option<&str>,
    right_ingredient: Option<&str>,
) -> Option<&'static str> {
    let subject_tokens = tokens(subject);
    if subject_tokens.is_empty() {
        return None;
    }
    let left_tokens = tokens(left_ingredient.unwrap_or(""));
    let right_tokens = tokens(right_ingredient.unwrap_or(""));
    let left_match = !left_tokens.is_empty()
        && (subject_tokens.is_subset(&left_tokens) || left_tokens.is_subset(&subject_tokens));
    let right_match = !right_tokens.is_empty()
        && (subject_tokens.is_subset(&right_tokens) || right_tokens.is_subset(&subject_tokens));
    if left_match == right_match {
        None
    } else if left_match {
        Some("left")
    } else {
        Some("right")
    }
}

fn tokens(value: &str) -> BTreeSet<String> {
    let normalized = value
        .nfkc()
        .flat_map(char::to_lowercase)
        .collect::<String>();
    token_regex()
        .find_iter(&normalized)
        .map(|token| token.as_str().to_owned())
        .collect()
}

fn canonical_ingredient(row: &Map<String, Value>) -> Option<&str> {
    text(row, "criterion_ingredient_name").or_else(|| text(row, "ingredient_name"))
}

fn canonical_paired_ingredient(row: &Map<String, Value>) -> Option<&str> {
    text(row, "criterion_paired_ingredient_name").or_else(|| text(row, "paired_ingredient_name"))
}

fn integer(row: &Map<String, Value>, key: &str) -> i64 {
    row.get(key).and_then(Value::as_i64).unwrap_or(0)
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}

fn within_hours_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?P<hours>\d+)\s*시간\s*이내\s*병용금기")
            .expect("valid interaction window regex")
    })
}

fn after_course_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(
            r"(?P<subject>[^,，.;。]{1,120}?)\s*투여\s*중\s*및\s*종료\s*후\s*(?P<amount>\d+)\s*(?P<unit>시간|일|주)\s*간",
        )
        .expect("valid washout regex")
    })
}

fn token_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"[0-9a-z가-힣]+").expect("valid interaction token regex"))
}
