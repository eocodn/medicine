use rusqlite::types::{Type, ValueRef};
use rusqlite::{params, Connection, Row};
use serde_json::{json, Map, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub(crate) fn manifest(con: &Connection) -> Result<Value, ()> {
    let meta = key_value_table(con, "canonical_meta")?;
    let mut statement = con
        .prepare(
            "SELECT dataset_key,source_family,sha256,row_count,fetched_at,effective_date
             FROM source_snapshots ORDER BY dataset_key",
        )
        .map_err(|_| ())?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, Option<String>>(4)?,
                row.get::<_, Option<String>>(5)?,
            ))
        })
        .map_err(|_| ())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| ())?;

    let mut families = BTreeSet::new();
    let mut invalid = Vec::new();
    let mut digest = Sha256::new();
    for (dataset_key, source_family, sha256, row_count, _, _) in &rows {
        families.insert(source_family.clone());
        let normalized_sha = sha256.to_ascii_lowercase();
        if !is_hex_sha256(&normalized_sha) || *row_count <= 0 {
            invalid.push(dataset_key.clone());
        }
        digest.update(format!("{dataset_key}\0{normalized_sha}\0{row_count}\n").as_bytes());
    }
    let provenance_dataset_id =
        (!rows.is_empty()).then(|| format!("sha256:{:x}", digest.finalize()));
    let provenance_verified = !rows.is_empty()
        && meta.get("schema_version").is_some_and(|value| {
            !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit())
        })
        && meta.get("build_stage").map(String::as_str) == Some("complete")
        && invalid.is_empty();

    if let Ok(contract_meta) = key_value_table(con, "reference_contract_meta") {
        let dataset_id = contract_meta
            .get("dataset_id")
            .map(|value| value.to_ascii_lowercase())
            .unwrap_or_default();
        let contract_major_text = contract_meta
            .get("contract_major")
            .cloned()
            .unwrap_or_default();
        let contract_major = parse_contract_major(&contract_major_text);
        let contract_verified = contract_major.is_some() && is_dataset_id(&dataset_id);
        return Ok(json!({
            "status": if contract_verified { "verified" } else { "not_verified" },
            "dataset_id": if contract_verified { Some(dataset_id) } else { None::<String> },
            "contract_major": contract_major,
            "schema_version": meta.get("schema_version"),
            "built_at": meta.get("built_at"),
            "source_count": rows.len(),
            "source_families": families.into_iter().collect::<Vec<_>>(),
            "invalid_sources": invalid,
            "missing_sources": [],
            "unexpected_sources": [],
            "misclassified_sources": [],
            "provenance_status": if provenance_verified { "verified" } else { "not_verified" },
            "provenance_dataset_id": provenance_dataset_id,
        }));
    }

    Ok(json!({
        "status": if provenance_verified { "verified" } else { "not_verified" },
        "dataset_id": provenance_dataset_id,
        "schema_version": meta.get("schema_version"),
        "built_at": meta.get("built_at"),
        "source_count": rows.len(),
        "source_families": families.into_iter().collect::<Vec<_>>(),
        "invalid_sources": invalid,
        "missing_sources": [],
        "unexpected_sources": [],
        "misclassified_sources": [],
    }))
}

pub(crate) fn linked_product_rows(
    con: &Connection,
    item_seq: &str,
    category: &str,
) -> Result<Vec<Map<String, Value>>, ()> {
    let mut statement = con
        .prepare(
            "SELECT * FROM product_rule_criteria
             WHERE item_seq=? AND category=?
             ORDER BY product_source_dataset_key,product_source_row,
                      criterion_source_dataset_key,criterion_source_row",
        )
        .map_err(|_| ())?;
    let columns = statement
        .column_names()
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let rows = statement
        .query_map(params![item_seq, category], |row| row_to_map(row, &columns))
        .map_err(|_| ())?;
    let mut result = Vec::new();
    for raw in rows {
        let mut row = raw.map_err(|_| ())?;
        alias_runtime_row(&mut row);
        result.push(row);
    }
    Ok(result)
}

pub(crate) fn unlinked_product_rules(
    con: &Connection,
    item_seq: &str,
    category: Option<&str>,
) -> Result<Vec<Map<String, Value>>, ()> {
    match category {
        Some(category) => query_maps(
            con,
            "SELECT r.* FROM product_rules r
             LEFT JOIN product_criterion_links l ON l.product_rule_id=r.id
             WHERE r.item_seq=? AND r.category=?
             GROUP BY r.id HAVING COUNT(l.criterion_rule_id)=0
             ORDER BY r.category,r.source_dataset_key,r.source_row",
            params![item_seq, category],
        ),
        None => query_maps(
            con,
            "SELECT r.* FROM product_rules r
             LEFT JOIN product_criterion_links l ON l.product_rule_id=r.id
             WHERE r.item_seq=?
             GROUP BY r.id HAVING COUNT(l.criterion_rule_id)=0
             ORDER BY r.category,r.source_dataset_key,r.source_row",
            [item_seq],
        ),
    }
}

fn query_maps<P: rusqlite::Params>(
    con: &Connection,
    sql: &str,
    params: P,
) -> Result<Vec<Map<String, Value>>, ()> {
    let mut statement = con.prepare(sql).map_err(|_| ())?;
    let columns = statement
        .column_names()
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let rows = statement
        .query_map(params, |row| row_to_map(row, &columns))
        .map_err(|_| ())?;
    rows.map(|row| row.map_err(|_| ())).collect()
}

pub(crate) fn category_resolution_issues(
    con: &Connection,
    item_seq: &str,
) -> Result<BTreeMap<String, Vec<Value>>, ()> {
    let mut issues: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for row in unlinked_product_rules(con, item_seq, None)? {
        let category = text(&row, "category").ok_or(())?.to_owned();
        if direct_rule_is_resolved(&row) {
            continue;
        }
        issues.entry(category).or_default().push(Value::Object(row));
    }
    Ok(issues)
}

pub(crate) fn has_unlinked_product_rule(
    con: &Connection,
    item_seq: &str,
    category: &str,
) -> Result<bool, ()> {
    Ok(!unlinked_product_rules(con, item_seq, Some(category))?.is_empty())
}

pub(crate) fn has_product_category(
    con: &Connection,
    item_seq: &str,
    category: &str,
) -> Result<bool, ()> {
    con.query_row(
        "SELECT EXISTS(
             SELECT 1 FROM product_rule_criteria WHERE item_seq=?1 AND category=?2
             UNION ALL
             SELECT 1 FROM product_rules WHERE item_seq=?1 AND category=?2
             UNION ALL
             SELECT 1 FROM product_flags WHERE item_seq=?1 AND category=?2
         )",
        params![item_seq, category],
        |row| row.get::<_, i64>(0),
    )
    .map(|value| value != 0)
    .map_err(|_| ())
}

fn key_value_table(con: &Connection, table: &str) -> Result<BTreeMap<String, String>, ()> {
    let sql = format!("SELECT key,value FROM {table}");
    let mut statement = con.prepare(&sql).map_err(|_| ())?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|_| ())?;
    rows.map(|row| row.map_err(|_| ())).collect()
}

fn alias_runtime_row(row: &mut Map<String, Value>) {
    copy_alias(row, "dataset_key", "criterion_source_dataset_key");
    copy_alias(row, "source_row", "criterion_source_row");
    copy_alias(row, "product_code", "item_seq");
    copy_alias(row, "paired_product_code", "paired_item_seq");
    copy_first_alias(
        row,
        "ingredient_name",
        &["criterion_ingredient_name", "ingredient_name"],
    );
    copy_first_alias(
        row,
        "paired_ingredient_name",
        &["criterion_paired_ingredient_name", "paired_ingredient_name"],
    );
    copy_alias(row, "rule_value", "criterion_rule_value");
    copy_alias(row, "dosage_form", "criterion_dosage_form");
    copy_alias(
        row,
        "maximum_daily_amount",
        "criterion_maximum_daily_amount",
    );
    copy_alias(row, "maximum_daily_unit", "criterion_maximum_daily_unit");
    copy_alias(row, "dose_parse_status", "criterion_dose_parse_status");
    copy_alias(row, "dose_parse_reason", "criterion_dose_parse_reason");
    copy_alias(row, "note", "criterion_note");
    copy_alias(row, "qualifier_note", "criterion_qualifier_note");
    copy_first_alias(row, "details", &["product_details", "criterion_details"]);
    row.insert("notice_no".to_owned(), Value::Null);
    row.insert("notice_date".to_owned(), Value::Null);
}

fn copy_alias(row: &mut Map<String, Value>, target: &str, source: &str) {
    let value = row.get(source).cloned().unwrap_or(Value::Null);
    row.insert(target.to_owned(), value);
}

fn copy_first_alias(row: &mut Map<String, Value>, target: &str, sources: &[&str]) {
    let value = sources
        .iter()
        .find_map(|source| row.get(*source).filter(|value| !value.is_null()).cloned())
        .unwrap_or(Value::Null);
    row.insert(target.to_owned(), value);
}

fn direct_rule_is_resolved(row: &Map<String, Value>) -> bool {
    match text(row, "category") {
        Some("elderly_caution") => true,
        Some("therapeutic_duplication_caution") => {
            text(row, "effect_name").is_some_and(|value| !value.trim().is_empty())
        }
        _ => false,
    }
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

fn parse_contract_major(value: &str) -> Option<u64> {
    if value.is_empty()
        || value.starts_with('0')
        || !value.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    value.parse::<u64>().ok().filter(|value| *value > 0)
}

fn is_dataset_id(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(is_hex_sha256)
}

fn is_hex_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}
