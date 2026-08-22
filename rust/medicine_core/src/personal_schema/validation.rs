use rusqlite::{Connection, Transaction};

use super::{SchemaError, SCHEMA_VERSION};

pub(super) fn reject_future_version(connection: &Connection) -> Result<(), SchemaError> {
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if version > SCHEMA_VERSION {
        return Err(SchemaError::InvalidSchema(format!(
            "unsupported schema version {version}"
        )));
    }
    Ok(())
}

pub(super) fn validate_required_columns(transaction: &Transaction<'_>) -> Result<(), SchemaError> {
    const TABLES: &[(&str, &[&str])] = &[
        (
            "people",
            &[
                "id",
                "name",
                "birth_date",
                "sex",
                "pregnancy_status",
                "lactation_status",
                "notes",
                "created_at",
            ],
        ),
        (
            "medications",
            &[
                "id",
                "person_id",
                "catalog_item_seq",
                "product_code",
                "product_name",
                "ingredient_code",
                "ingredient_name",
                "manufacturer",
                "catalog_source",
                "dosage_text",
                "dose_amount",
                "dose_unit",
                "frequency_per_day",
                "meal_relation",
                "administration_route",
                "as_needed",
                "prn_max_per_day",
                "prescription_days",
                "long_term",
                "start_date",
                "end_date",
                "active",
                "stopped_at",
                "source",
                "revision",
                "created_at",
                "updated_at",
            ],
        ),
        (
            "medication_schedules",
            &[
                "id",
                "medication_id",
                "time_of_day",
                "dose_text",
                "created_at",
            ],
        ),
        (
            "dose_logs",
            &[
                "id",
                "medication_id",
                "person_id",
                "dose_instance_id",
                "status",
                "occurred_at",
                "note",
                "product_name_snapshot",
                "dosage_text_snapshot",
                "created_at",
            ],
        ),
        (
            "dose_instances",
            &[
                "id",
                "medication_id",
                "person_id",
                "scheduled_date",
                "schedule_key",
                "scheduled_time",
                "slot_label",
                "dose_text",
                "product_name_snapshot",
                "ingredient_name_snapshot",
                "status",
                "completed_at",
                "created_at",
            ],
        ),
        (
            "medication_revisions",
            &[
                "medication_id",
                "revision",
                "action",
                "snapshot_json",
                "assessment_json",
                "acknowledged",
                "request_id",
                "payload_hash",
                "created_at",
            ],
        ),
        (
            "medication_requests",
            &[
                "request_id",
                "person_id",
                "payload_hash",
                "medication_id",
                "created_at",
            ],
        ),
        (
            "prn_requests",
            &[
                "request_id",
                "medication_id",
                "person_id",
                "payload_hash",
                "dose_instance_id",
                "state",
                "created_at",
            ],
        ),
    ];
    for (table, required) in TABLES {
        let existing = table_columns(transaction, table)?;
        for column in *required {
            if !existing.iter().any(|existing| existing == column) {
                return Err(SchemaError::InvalidSchema(format!(
                    "{table}.{column} is missing"
                )));
            }
        }
    }
    Ok(())
}

fn table_columns(transaction: &Transaction<'_>, table: &str) -> Result<Vec<String>, SchemaError> {
    let mut statement = transaction.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}
