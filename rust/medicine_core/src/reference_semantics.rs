use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

const SUPPORTED_RUNTIME_EVALUATORS: [&str; 2] = ["minimum_separation", "excluded_route"];
const SUPPORTED_EXCLUDED_ROUTES: [&str; 7] = [
    "oral",
    "injection",
    "ophthalmic",
    "otic",
    "nasal",
    "inhaled",
    "topical",
];

pub(crate) fn qualifiers(con: &Connection, row: &Map<String, Value>) -> Result<Vec<Value>, ()> {
    let semantics = semantics(con, row)?;
    Ok(semantics
        .into_iter()
        .map(|semantic| {
            let informational = text(&semantic, "semantic_role") == Some("informational");
            json!({
                "type": semantic.get("qualifier_type").cloned().unwrap_or(Value::Null),
                "text": semantic.get("display_text").cloned().unwrap_or(Value::Null),
                "source_remark": semantic.get("source_remark").cloned().unwrap_or(Value::Null),
                "mode": if informational { "informational" } else { "condition" },
                "requires_review": semantic_requires_review(&semantic),
            })
        })
        .collect())
}

pub(crate) fn criterion_note_requires_review(
    con: &Connection,
    row: &Map<String, Value>,
) -> Result<bool, ()> {
    let semantics = semantics(con, row)?;
    if semantics.is_empty() {
        return Ok(false);
    }
    if text(row, "match_method") == Some("mfds_unanimous_value") {
        return Ok(true);
    }
    Ok(semantics.iter().any(semantic_requires_review))
}

pub(crate) fn has_semantics(con: &Connection, row: &Map<String, Value>) -> Result<bool, ()> {
    Ok(!semantics(con, row)?.is_empty())
}

pub(crate) fn semantic_records(
    con: &Connection,
    row: &Map<String, Value>,
) -> Result<Vec<Map<String, Value>>, ()> {
    semantics(con, row)
}

pub(crate) fn dedupe_qualifiers(values: Vec<Value>) -> Vec<Value> {
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for value in values {
        let key = (
            value.get("type").map(Value::to_string),
            value.get("text").map(Value::to_string),
            value.get("source_remark").map(Value::to_string),
        );
        if seen.insert(key) {
            result.push(value);
        }
    }
    result
}

fn semantics(con: &Connection, row: &Map<String, Value>) -> Result<Vec<Map<String, Value>>, ()> {
    let Some(criterion_rule_id) = row.get("criterion_rule_id").and_then(Value::as_i64) else {
        return Ok(direct_rule_semantics(row));
    };
    let expectation = con
        .query_row(
            "SELECT expected_fact_count FROM reference_semantic_expectations WHERE criterion_rule_id=?",
            [criterion_rule_id],
            |record| record.get::<_, i64>(0),
        )
        .optional()
        .map_err(|_| ())?;
    let mut statement = con
        .prepare(
            "SELECT semantic_role,evaluation_mode,evaluator_kind,fallback_action,
                    qualifier_type,display_text,structured_payload_json,source_remark
             FROM reference_criterion_semantics
             WHERE criterion_rule_id=? ORDER BY ordinal",
        )
        .map_err(|_| ())?;
    let records = statement
        .query_map([criterion_rule_id], |record| {
            Ok((
                record.get::<_, String>(0)?,
                record.get::<_, String>(1)?,
                record.get::<_, String>(2)?,
                record.get::<_, String>(3)?,
                record.get::<_, String>(4)?,
                record.get::<_, String>(5)?,
                record.get::<_, String>(6)?,
                record.get::<_, String>(7)?,
            ))
        })
        .map_err(|_| ())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| ())?;
    if expectation.is_some_and(|expected| records.len() != expected as usize) {
        return Ok(vec![missing_contract_semantic(row)]);
    }
    let mut result = Vec::with_capacity(records.len());
    for (
        semantic_role,
        evaluation_mode,
        evaluator_kind,
        fallback_action,
        qualifier_type,
        display_text,
        structured_payload_json,
        source_remark,
    ) in records
    {
        let structured_payload = match serde_json::from_str::<Value>(&structured_payload_json) {
            Ok(Value::Object(value)) => Value::Object(value),
            _ => {
                result.push(review_required_semantic(
                    &qualifier_type,
                    &display_text,
                    &source_remark,
                    None,
                ));
                continue;
            }
        };
        if !semantic_record_is_valid(
            &semantic_role,
            &evaluation_mode,
            &evaluator_kind,
            &fallback_action,
            &structured_payload,
        ) {
            result.push(review_required_semantic(
                &qualifier_type,
                &display_text,
                &source_remark,
                None,
            ));
            continue;
        }
        let mut semantic = Map::new();
        semantic.insert("semantic_role".to_owned(), Value::String(semantic_role));
        semantic.insert("evaluation_mode".to_owned(), Value::String(evaluation_mode));
        semantic.insert("evaluator_kind".to_owned(), Value::String(evaluator_kind));
        semantic.insert("fallback_action".to_owned(), Value::String(fallback_action));
        semantic.insert("qualifier_type".to_owned(), Value::String(qualifier_type));
        semantic.insert("display_text".to_owned(), Value::String(display_text));
        semantic.insert("structured_payload".to_owned(), structured_payload);
        semantic.insert("source_remark".to_owned(), Value::String(source_remark));
        result.push(semantic);
    }
    Ok(result)
}

fn semantic_record_is_valid(
    role: &str,
    mode: &str,
    evaluator: &str,
    fallback: &str,
    payload: &Value,
) -> bool {
    let expected = match evaluator {
        "display_only" => Some(("informational", "resolved_at_build", "none")),
        "opaque_condition" => Some((
            "applicability_condition",
            "review_required",
            "review_required",
        )),
        "minimum_separation" | "excluded_route" => Some((
            "applicability_condition",
            "runtime_evaluable",
            "review_required",
        )),
        _ => None,
    };
    if let Some(expected) = expected {
        if (role, mode, fallback) != expected {
            return false;
        }
    } else if !(role == "applicability_condition"
        && mode == "runtime_evaluable"
        && fallback == "review_required")
    {
        return false;
    }

    match evaluator {
        "minimum_separation" => {
            payload
                .get("hours")
                .and_then(Value::as_i64)
                .is_some_and(|hours| hours > 0)
                && payload.get("direction").and_then(Value::as_str) == Some("symmetric")
        }
        "excluded_route" => payload
            .get("route")
            .and_then(Value::as_str)
            .is_some_and(|route| SUPPORTED_EXCLUDED_ROUTES.contains(&route)),
        _ => true,
    }
}

fn direct_rule_semantics(row: &Map<String, Value>) -> Vec<Map<String, Value>> {
    let dataset_key = text(row, "criterion_source_dataset_key")
        .or_else(|| text(row, "dataset_key"))
        .unwrap_or("");
    let remark = text(row, "criterion_qualifier_note")
        .or_else(|| text(row, "qualifier_note"))
        .unwrap_or("")
        .trim();
    if !dataset_key.starts_with("mfds_dur_ingredient:") || remark.is_empty() {
        return Vec::new();
    }
    vec![review_required_semantic(
        "source_note",
        remark,
        remark,
        None,
    )]
}

fn missing_contract_semantic(row: &Map<String, Value>) -> Map<String, Value> {
    let source_remark = text(row, "criterion_qualifier_note").unwrap_or("").trim();
    review_required_semantic(
        "source_note",
        if source_remark.is_empty() {
            "세부 적용 조건 확인 필요"
        } else {
            source_remark
        },
        source_remark,
        Some("missing_contract_semantics"),
    )
}

fn review_required_semantic(
    qualifier_type: &str,
    display_text: &str,
    source_remark: &str,
    runtime_error_kind: Option<&str>,
) -> Map<String, Value> {
    let mut semantic = Map::new();
    semantic.insert(
        "semantic_role".to_owned(),
        Value::String("applicability_condition".to_owned()),
    );
    semantic.insert(
        "evaluation_mode".to_owned(),
        Value::String("review_required".to_owned()),
    );
    semantic.insert(
        "evaluator_kind".to_owned(),
        Value::String("opaque_condition".to_owned()),
    );
    semantic.insert(
        "fallback_action".to_owned(),
        Value::String("review_required".to_owned()),
    );
    semantic.insert(
        "qualifier_type".to_owned(),
        Value::String(qualifier_type.to_owned()),
    );
    semantic.insert(
        "display_text".to_owned(),
        Value::String(display_text.to_owned()),
    );
    semantic.insert("structured_payload".to_owned(), json!({}));
    semantic.insert(
        "source_remark".to_owned(),
        Value::String(source_remark.to_owned()),
    );
    if let Some(runtime_error_kind) = runtime_error_kind {
        semantic.insert(
            "runtime_error_kind".to_owned(),
            Value::String(runtime_error_kind.to_owned()),
        );
    }
    semantic
}

fn semantic_requires_review(semantic: &Map<String, Value>) -> bool {
    match text(semantic, "evaluation_mode") {
        Some("review_required") => true,
        Some("runtime_evaluable") => {
            let evaluator = text(semantic, "evaluator_kind").unwrap_or("");
            !SUPPORTED_RUNTIME_EVALUATORS.contains(&evaluator)
                && text(semantic, "fallback_action") == Some("review_required")
        }
        _ => false,
    }
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}
