use rusqlite::Connection;
use serde_json::{json, Map, Value};
use std::cmp::Ordering;
use std::collections::BTreeSet;

use crate::dose_quantity::{draft_quantity, frequency, is_count_unit, source_quantity};
use crate::reference_runtime::{has_unlinked_product_rule, linked_product_rows};
use crate::reference_semantics::{criterion_note_requires_review, dedupe_qualifiers, qualifiers};

pub(crate) fn evaluate(con: &Connection, product: &Value, draft: &Value) -> Result<Value, ()> {
    let product = product.as_object().ok_or(())?;
    let draft = draft.as_object().ok_or(())?;
    let item_seq = product
        .get("catalog_item_seq")
        .or_else(|| product.get("product_ref"))
        .or_else(|| product.get("product_code"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let duration_rows = match item_seq {
        Some(item_seq) => linked_product_rows(con, item_seq, "duration_caution")?,
        None => Vec::new(),
    };
    let dose_rows = match item_seq {
        Some(item_seq) => linked_product_rows(con, item_seq, "dose_caution")?,
        None => Vec::new(),
    };
    let duration = evaluate_duration(con, item_seq, draft, &duration_rows)?;
    let dose = evaluate_dose(con, item_seq, product, draft, &dose_rows)?;
    Ok(json!({"duration": duration, "dose": dose}))
}

fn evaluate_duration(
    con: &Connection,
    item_seq: Option<&str>,
    draft: &Map<String, Value>,
    rows: &[Map<String, Value>],
) -> Result<Value, ()> {
    let mut result = base_dimension();
    let prescription_days = draft
        .get("prescription_days")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0);
    let mut distinct_days = BTreeSet::new();
    let mut parse_reasons = BTreeSet::new();
    for row in rows {
        match duration_limit_days(row.get("rule_value")) {
            (Some(days), None) => {
                distinct_days.insert(days);
            }
            (_, Some(reason)) => {
                parse_reasons.insert(reason);
            }
            _ => {}
        }
    }
    attach_qualifiers(con, rows, &mut result)?;
    let qualifier_review = any_qualifier_review(con, rows)?;

    if rows.is_empty() {
        let unlinked = match item_seq {
            Some(item_seq) => has_unlinked_product_rule(con, item_seq, "duration_caution")?,
            None => false,
        };
        if unlinked {
            result.insert(
                "reason".to_owned(),
                Value::String(
                    "canonical duration product rule is not linked to one criterion".to_owned(),
                ),
            );
        } else {
            result.insert(
                "result".to_owned(),
                Value::String("not_applicable".to_owned()),
            );
        }
    } else if qualifier_review {
        result.insert(
            "reason".to_owned(),
            Value::String(
                "MFDS duration criterion has a qualifier requiring professional review".to_owned(),
            ),
        );
        result.insert(
            "evaluation_status".to_owned(),
            Value::String("conditional".to_owned()),
        );
    } else if prescription_days.is_none() {
        result.insert(
            "reason".to_owned(),
            Value::String("prescription duration is missing or invalid".to_owned()),
        );
    } else if !parse_reasons.is_empty() || distinct_days.len() != 1 {
        let reason = if parse_reasons.len() == 1 && distinct_days.is_empty() {
            parse_reasons.iter().next().cloned().unwrap_or_default()
        } else {
            "duration rule is missing, malformed, or ambiguous".to_owned()
        };
        result.insert("reason".to_owned(), Value::String(reason));
    } else {
        let requested = prescription_days.ok_or(())?;
        let maximum = *distinct_days.iter().next().ok_or(())?;
        result.insert(
            "result".to_owned(),
            Value::String(
                if requested > maximum {
                    "exceeded"
                } else {
                    "within"
                }
                .to_owned(),
            ),
        );
        result.insert("requested_days".to_owned(), Value::from(requested));
        result.insert("maximum_days".to_owned(), Value::from(maximum));
    }
    Ok(with_source_rows(result, rows))
}

fn evaluate_dose(
    con: &Connection,
    item_seq: Option<&str>,
    product: &Map<String, Value>,
    draft: &Map<String, Value>,
    rows: &[Map<String, Value>],
) -> Result<Value, ()> {
    let mut result = base_dimension();
    let (source, source_reason) = if rows.is_empty() {
        (None, None)
    } else {
        source_quantity(rows)
    };
    let product_dosage_forms = rows
        .iter()
        .filter_map(|row| text(row, "product_dosage_form"))
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let joined_forms = product_dosage_forms.join(", ");
    let effective_form = if joined_forms.is_empty() {
        product
            .get("dosage_form")
            .and_then(Value::as_str)
            .unwrap_or("")
    } else {
        joined_forms.as_str()
    };
    let (entered, entered_reason) = draft_quantity(draft, effective_form);
    let (frequency, frequency_reason) = frequency(draft);
    attach_qualifiers(con, rows, &mut result)?;
    let qualifier_review = any_qualifier_review(con, rows)?;

    if rows.is_empty() {
        let unlinked = match item_seq {
            Some(item_seq) => has_unlinked_product_rule(con, item_seq, "dose_caution")?,
            None => false,
        };
        if unlinked {
            result.insert(
                "reason".to_owned(),
                Value::String(
                    "canonical dose product rule is not linked to one criterion".to_owned(),
                ),
            );
        } else {
            result.insert(
                "result".to_owned(),
                Value::String("not_applicable".to_owned()),
            );
        }
    } else if qualifier_review {
        result.insert(
            "reason".to_owned(),
            Value::String(
                "MFDS dose criterion has a qualifier requiring professional review".to_owned(),
            ),
        );
        result.insert(
            "evaluation_status".to_owned(),
            Value::String("conditional".to_owned()),
        );
    } else if source.is_none() {
        result.insert(
            "reason".to_owned(),
            Value::String(source_reason.unwrap_or_else(|| "dose rule is missing".to_owned())),
        );
    } else if entered.is_none() {
        result.insert(
            "reason".to_owned(),
            Value::String(
                entered_reason.unwrap_or_else(|| {
                    "dose input is missing or has an unsupported unit".to_owned()
                }),
            ),
        );
    } else if frequency.is_none() {
        result.insert(
            "reason".to_owned(),
            Value::String(frequency_reason.unwrap_or_else(|| {
                "daily frequency is missing (PRN without frequency is not evaluable)".to_owned()
            })),
        );
    } else {
        let (threshold_amount, threshold_unit) = source.ok_or(())?;
        let (entered_amount, entered_unit) = entered.ok_or(())?;
        let frequency = frequency.ok_or(())?;
        let daily_amount = if threshold_unit == "mg" && entered_unit == "mg" {
            entered_amount.checked_mul_u32(frequency)
        } else if threshold_unit == "mg" && is_count_unit(&entered_unit) {
            result.insert(
                "reason".to_owned(),
                Value::String(
                    "count dose requires an authoritative per-unit ingredient content".to_owned(),
                ),
            );
            None
        } else if is_count_unit(&threshold_unit) && entered_unit == threshold_unit {
            entered_amount.checked_mul_u32(frequency)
        } else {
            result.insert(
                "reason".to_owned(),
                Value::String("dose input and source threshold use incomparable units".to_owned()),
            );
            None
        };
        if let Some(daily_amount) = daily_amount {
            let exceeded =
                daily_amount.numeric_cmp(threshold_amount).ok_or(())? == Ordering::Greater;
            result.insert(
                "result".to_owned(),
                Value::String(if exceeded { "exceeded" } else { "within" }.to_owned()),
            );
            result.insert(
                "daily_amount".to_owned(),
                daily_amount.to_json_number().ok_or(())?,
            );
            result.insert(
                "maximum_daily_amount".to_owned(),
                threshold_amount.to_json_number().ok_or(())?,
            );
            result.insert("unit".to_owned(), Value::String(threshold_unit));
        }
    }
    Ok(with_source_rows(result, rows))
}

fn base_dimension() -> Map<String, Value> {
    let mut result = Map::new();
    result.insert(
        "result".to_owned(),
        Value::String("not_evaluable".to_owned()),
    );
    result.insert(
        "source_scope".to_owned(),
        Value::String("canonical_product".to_owned()),
    );
    result
}

fn duration_limit_days(value: Option<&Value>) -> (Option<i64>, Option<String>) {
    let text = value
        .and_then(|value| match value {
            Value::String(value) => Some(value.trim().to_owned()),
            Value::Number(value) => Some(value.to_string()),
            _ => None,
        })
        .unwrap_or_default();
    if text.is_empty() {
        return malformed_duration();
    }
    let digit_count = text
        .bytes()
        .take_while(|byte| byte.is_ascii_digit())
        .count();
    if digit_count == 0 {
        return malformed_duration();
    }
    let amount = match text[..digit_count].parse::<i64>() {
        Ok(value) if value > 0 => value,
        _ => return malformed_duration(),
    };
    match text[digit_count..].trim() {
        "" | "일" => (Some(amount), None),
        "주" => match amount.checked_mul(7) {
            Some(days) => (Some(days), None),
            None => malformed_duration(),
        },
        "개월" => (
            None,
            Some("month-based duration rule requires calendar-aware evaluation".to_owned()),
        ),
        _ => malformed_duration(),
    }
}

fn malformed_duration() -> (Option<i64>, Option<String>) {
    (
        None,
        Some("duration rule is missing, malformed, or ambiguous".to_owned()),
    )
}

fn attach_qualifiers(
    con: &Connection,
    rows: &[Map<String, Value>],
    result: &mut Map<String, Value>,
) -> Result<(), ()> {
    let mut values = Vec::new();
    for row in rows {
        values.extend(qualifiers(con, row)?);
    }
    let values = dedupe_qualifiers(values);
    if !values.is_empty() {
        result.insert("qualifiers".to_owned(), Value::Array(values));
    }
    Ok(())
}

fn any_qualifier_review(con: &Connection, rows: &[Map<String, Value>]) -> Result<bool, ()> {
    for row in rows {
        if criterion_note_requires_review(con, row)? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn with_source_rows(mut result: Map<String, Value>, rows: &[Map<String, Value>]) -> Value {
    let values = rows.iter().cloned().map(Value::Object).collect::<Vec<_>>();
    result.insert("source_rows".to_owned(), Value::Array(values.clone()));
    if values.len() == 1 {
        result.insert("source".to_owned(), values[0].clone());
    }
    Value::Object(result)
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}
