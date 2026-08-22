//! Read-only verification of the contract-v1 reference database.

use rusqlite::{Connection, OpenFlags, Row};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

const CONTRACT_MAJOR: u64 = 1;
const REQUIRED_COLUMNS: [(&str, &[&str]); 11] = [
    ("reference_contract_meta", &["key", "value"]),
    ("canonical_meta", &["key", "value"]),
    (
        "source_snapshots",
        &[
            "dataset_key",
            "source_family",
            "effective_date",
            "fetched_at",
            "row_count",
            "sha256",
        ],
    ),
    (
        "products",
        &[
            "item_seq",
            "product_name",
            "manufacturer",
            "ingredient_text",
            "dosage_form",
            "permit_date",
            "cancel_date",
            "cancel_name",
            "permit_status",
        ],
    ),
    ("product_identifiers", &["item_seq", "system", "value"]),
    (
        "product_flags",
        &[
            "item_seq",
            "category",
            "flag_code",
            "flag_name",
            "ingredient_name",
            "dosage_form",
            "details",
            "change_date",
            "source_dataset_key",
            "source_row",
            "flag_ordinal",
        ],
    ),
    (
        "product_rules",
        &[
            "id",
            "source_dataset_key",
            "source_row",
            "category",
            "item_seq",
            "paired_item_seq",
            "effect_name",
            "dosage_form",
            "details",
        ],
    ),
    (
        "product_criterion_links",
        &["product_rule_id", "criterion_rule_id"],
    ),
    (
        "product_rule_criteria",
        &[
            "criterion_rule_id",
            "product_source_dataset_key",
            "product_source_row",
            "criterion_source_dataset_key",
            "criterion_source_row",
            "category",
            "item_seq",
            "ingredient_name",
            "paired_item_seq",
            "paired_ingredient_name",
            "effect_name",
            "product_dosage_form",
            "product_details",
            "criterion_ingredient_name",
            "criterion_paired_ingredient_name",
            "criterion_rule_value",
            "criterion_dosage_form",
            "criterion_note",
            "criterion_qualifier_note",
            "criterion_details",
            "criterion_maximum_daily_amount",
            "criterion_maximum_daily_unit",
            "criterion_dose_parse_status",
            "criterion_dose_parse_reason",
            "match_method",
        ],
    ),
    (
        "reference_semantic_expectations",
        &["criterion_rule_id", "expected_fact_count"],
    ),
    (
        "reference_criterion_semantics",
        &[
            "criterion_rule_id",
            "ordinal",
            "semantic_role",
            "evaluation_mode",
            "evaluator_kind",
            "fallback_action",
            "qualifier_type",
            "display_text",
            "structured_payload_json",
            "source_remark",
        ],
    ),
];
const SUPPORTED_EXCLUDED_ROUTES: [&str; 7] = [
    "oral",
    "injection",
    "ophthalmic",
    "otic",
    "nasal",
    "inhaled",
    "topical",
];

/// The result of a complete, read-only contract-v1 verification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceVerificationReport {
    pub status: String,
    pub contract_major: u64,
    pub dataset_id: String,
    pub size_bytes: u64,
}

/// A verification failure. Messages identify the failed boundary for update diagnostics.
#[derive(Debug)]
pub enum ReferenceVerificationError {
    InvalidArgument(String),
    Io(std::io::Error),
    Sqlite(rusqlite::Error),
    Contract(String),
    Schema(String),
    Semantic(String),
    Integrity(String),
}

impl Display for ReferenceVerificationError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidArgument(message) => {
                write!(formatter, "invalid reference input: {message}")
            }
            Self::Io(error) => write!(formatter, "reference database I/O failed: {error}"),
            Self::Sqlite(error) => write!(formatter, "reference SQLite operation failed: {error}"),
            Self::Contract(message) => write!(
                formatter,
                "reference contract verification failed: {message}"
            ),
            Self::Schema(message) => write!(
                formatter,
                "reference contract schema verification failed: {message}"
            ),
            Self::Semantic(message) => write!(
                formatter,
                "reference semantic verification failed: {message}"
            ),
            Self::Integrity(message) => write!(
                formatter,
                "reference SQLite integrity verification failed: {message}"
            ),
        }
    }
}
impl Error for ReferenceVerificationError {}
impl From<std::io::Error> for ReferenceVerificationError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}
impl From<rusqlite::Error> for ReferenceVerificationError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

/// Verify a reference database against the contract-v1 logical surface.
pub fn verify_reference_database(
    path: &Path,
    expected_contract_major: u64,
    expected_dataset_id: &str,
) -> Result<ReferenceVerificationReport, ReferenceVerificationError> {
    if expected_contract_major != CONTRACT_MAJOR {
        return Err(ReferenceVerificationError::InvalidArgument(
            "reference contract major is unsupported by this runtime".to_owned(),
        ));
    }
    let expected_dataset_id = expected_dataset_id.to_ascii_lowercase();
    validate_dataset_id(&expected_dataset_id)
        .map_err(ReferenceVerificationError::InvalidArgument)?;
    let size_bytes = std::fs::metadata(path)?.len();

    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    connection.pragma_update(None, "query_only", true)?;
    verify_sqlite_integrity(&connection)?;
    verify_schema(&connection)?;
    verify_semantic_materialization(&connection)?;

    let metadata = read_contract_metadata(&connection)?;
    let contract_major = metadata
        .get("contract_major")
        .ok_or_else(|| {
            ReferenceVerificationError::Contract("metadata is missing contract_major".to_owned())
        })
        .and_then(|value| parse_contract_major(value))?;
    if contract_major != expected_contract_major {
        return Err(ReferenceVerificationError::Contract(
            "reference contract major does not match release".to_owned(),
        ));
    }
    let dataset_id = metadata
        .get("dataset_id")
        .ok_or_else(|| {
            ReferenceVerificationError::Contract("metadata is missing dataset_id".to_owned())
        })?
        .to_ascii_lowercase();
    validate_dataset_id(&dataset_id).map_err(ReferenceVerificationError::Contract)?;
    if dataset_id != expected_dataset_id {
        return Err(ReferenceVerificationError::Contract(
            "reference dataset identity does not match release".to_owned(),
        ));
    }
    Ok(ReferenceVerificationReport {
        status: "verified".to_owned(),
        contract_major,
        dataset_id,
        size_bytes,
    })
}

fn parse_contract_major(value: &str) -> Result<u64, ReferenceVerificationError> {
    let value = value.trim();
    if value.is_empty()
        || value.starts_with('0')
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return Err(ReferenceVerificationError::Contract(
            "metadata contract_major is invalid".to_owned(),
        ));
    }
    value.parse().map_err(|_| {
        ReferenceVerificationError::Contract("metadata contract_major is invalid".to_owned())
    })
}

fn validate_dataset_id(value: &str) -> Result<(), String> {
    let Some(hex) = value.strip_prefix("sha256:") else {
        return Err("invalid expected reference dataset identity".to_owned());
    };
    if hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("invalid expected reference dataset identity".to_owned());
    }
    Ok(())
}

fn verify_sqlite_integrity(connection: &Connection) -> Result<(), ReferenceVerificationError> {
    let integrity: String = connection.query_row("PRAGMA integrity_check", [], |row| row.get(0))?;
    if integrity != "ok" {
        return Err(ReferenceVerificationError::Integrity(format!(
            "integrity_check returned {integrity}"
        )));
    }
    let mut statement = connection.prepare("PRAGMA foreign_key_check")?;
    let mut rows = statement.query([])?;
    if rows.next()?.is_some() {
        let mut count = 1;
        while rows.next()?.is_some() {
            count += 1;
        }
        return Err(ReferenceVerificationError::Integrity(format!(
            "foreign key check found {count} violation(s)"
        )));
    }
    Ok(())
}

fn verify_schema(connection: &Connection) -> Result<(), ReferenceVerificationError> {
    let mut objects = BTreeMap::new();
    let mut statement = connection.prepare(
        "SELECT name,type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    for row in rows {
        let (name, kind) = row?;
        objects.insert(name, kind);
    }
    for (name, required_columns) in REQUIRED_COLUMNS {
        let Some(kind) = objects.get(name) else {
            return Err(ReferenceVerificationError::Schema(format!(
                "reference contract schema is missing object: {name}"
            )));
        };
        if kind != "table" && kind != "view" {
            return Err(ReferenceVerificationError::Schema(format!(
                "reference contract object {name} has unsupported kind {kind}"
            )));
        }
        let actual_columns = table_columns(connection, name)?;
        let actual_columns: BTreeSet<&str> = actual_columns.iter().map(String::as_str).collect();
        let missing: Vec<&str> = required_columns
            .iter()
            .copied()
            .filter(|column| !actual_columns.contains(column))
            .collect();
        if !missing.is_empty() {
            return Err(ReferenceVerificationError::Schema(format!(
                "reference contract schema object {name} is missing columns: {}",
                missing.join(", ")
            )));
        }
        query_projection(connection, name, required_columns)?;
    }
    Ok(())
}

fn table_columns(
    connection: &Connection,
    name: &str,
) -> Result<Vec<String>, ReferenceVerificationError> {
    let pragma = format!("PRAGMA table_info([{name}])");
    let mut statement = connection.prepare(&pragma)?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
}

fn query_projection(
    connection: &Connection,
    name: &str,
    columns: &[&str],
) -> Result<(), ReferenceVerificationError> {
    let projection = columns
        .iter()
        .map(|column| format!("[{column}]"))
        .collect::<Vec<_>>()
        .join(",");
    let query = format!("SELECT {projection} FROM [{name}] LIMIT 0");
    let mut statement = connection.prepare(&query)?;
    let mut rows = statement.query([])?;
    rows.next()?;
    Ok(())
}

fn read_contract_metadata(
    connection: &Connection,
) -> Result<BTreeMap<String, String>, ReferenceVerificationError> {
    let mut statement = connection.prepare("SELECT key,value FROM reference_contract_meta")?;
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    rows.collect::<Result<BTreeMap<_, _>, _>>()
        .map_err(Into::into)
}

fn verify_semantic_materialization(
    connection: &Connection,
) -> Result<(), ReferenceVerificationError> {
    let mut expectations = BTreeMap::new();
    let mut expectation_statement = connection.prepare(
        "SELECT criterion_rule_id,expected_fact_count FROM reference_semantic_expectations ORDER BY criterion_rule_id",
    )?;
    let expectation_rows = expectation_statement
        .query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)))?;
    for row in expectation_rows {
        let (criterion, expected_count) = row?;
        if expected_count <= 0 {
            return Err(ReferenceVerificationError::Semantic(
                "reference semantic expectation count is invalid".to_owned(),
            ));
        }
        expectations.insert(criterion, expected_count);
    }

    let mut actual_counts = BTreeMap::<i64, i64>::new();
    let mut seen_ordinals = BTreeSet::new();
    let mut statement = connection.prepare(
        "SELECT criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,\
                fallback_action,qualifier_type,display_text,structured_payload_json,source_remark \
         FROM reference_criterion_semantics ORDER BY criterion_rule_id,ordinal",
    )?;
    let rows = statement.query_map([], semantic_row)?;
    for row in rows {
        let row = row?;
        if !seen_ordinals.insert((row.criterion_rule_id, row.ordinal)) {
            return Err(ReferenceVerificationError::Semantic(format!(
                "duplicate semantic ordinal {}:{}",
                row.criterion_rule_id, row.ordinal
            )));
        }
        *actual_counts.entry(row.criterion_rule_id).or_default() += 1;
        validate_semantic_row(&row)?;
    }
    if actual_counts.keys().collect::<BTreeSet<_>>() != expectations.keys().collect::<BTreeSet<_>>()
    {
        return Err(ReferenceVerificationError::Semantic(
            "reference semantic expectations do not cover materialized semantic rows".to_owned(),
        ));
    }
    for (criterion, expected_count) in expectations {
        if actual_counts.get(&criterion).copied().unwrap_or_default() != expected_count {
            return Err(ReferenceVerificationError::Semantic(format!(
                "reference semantic materialization count does not match expectation for criterion {criterion}"
            )));
        }
    }
    Ok(())
}

struct SemanticRow {
    criterion_rule_id: i64,
    ordinal: i64,
    semantic_role: String,
    evaluation_mode: String,
    evaluator_kind: String,
    fallback_action: String,
    payload: Value,
}

fn semantic_row(row: &Row<'_>) -> rusqlite::Result<SemanticRow> {
    let payload = row.get::<_, String>(8)?;
    let payload = serde_json::from_str(&payload).unwrap_or(Value::Null);
    Ok(SemanticRow {
        criterion_rule_id: row.get(0)?,
        ordinal: row.get(1)?,
        semantic_role: row.get(2)?,
        evaluation_mode: row.get(3)?,
        evaluator_kind: row.get(4)?,
        fallback_action: row.get(5)?,
        payload,
    })
}

fn validate_semantic_row(row: &SemanticRow) -> Result<(), ReferenceVerificationError> {
    let expected = match row.evaluator_kind.as_str() {
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
    if let Some((role, mode, fallback)) = expected {
        if (
            row.semantic_role.as_str(),
            row.evaluation_mode.as_str(),
            row.fallback_action.as_str(),
        ) != (role, mode, fallback)
        {
            return Err(invalid_semantic(
                row,
                "mode/fallback does not match evaluator",
            ));
        }
    } else if row.semantic_role != "applicability_condition"
        || row.evaluation_mode != "runtime_evaluable"
        || row.fallback_action != "review_required"
    {
        return Err(invalid_semantic(
            row,
            "unknown evaluator lacks conservative fallback",
        ));
    }
    if !row.payload.is_object() {
        return Err(invalid_semantic(
            row,
            "structured payload must be a JSON object",
        ));
    }
    match row.evaluator_kind.as_str() {
        "minimum_separation" => {
            let hours = row.payload.get("hours").and_then(Value::as_i64);
            if hours.is_none_or(|hours| hours <= 0)
                || row.payload.get("direction").and_then(Value::as_str) != Some("symmetric")
            {
                return Err(invalid_semantic(
                    row,
                    "minimum_separation payload is invalid",
                ));
            }
        }
        "excluded_route" => {
            if !row
                .payload
                .get("route")
                .and_then(Value::as_str)
                .is_some_and(|route| SUPPORTED_EXCLUDED_ROUTES.contains(&route))
            {
                return Err(invalid_semantic(row, "excluded_route payload is invalid"));
            }
        }
        _ => {}
    }
    Ok(())
}

fn invalid_semantic(row: &SemanticRow, message: &str) -> ReferenceVerificationError {
    ReferenceVerificationError::Semantic(format!(
        "reference semantic row {}:{} is invalid: {message}",
        row.criterion_rule_id, row.ordinal
    ))
}
