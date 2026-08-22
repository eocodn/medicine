use rusqlite::{Connection, OpenFlags, OptionalExtension};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Duration;

pub(crate) enum ProductError {
    BadRequest(String),
    NotFound(String),
    Unavailable,
    Internal,
}

pub(crate) fn inspect(canonical_db: Option<&Path>, product_ref: &str) -> (u16, Value) {
    let result = resolve(canonical_db, product_ref);
    match result {
        Ok(body) => (200, body),
        Err(ProductError::BadRequest(detail)) => (400, json!({"detail": detail})),
        Err(ProductError::NotFound(detail)) => (404, json!({"detail": detail})),
        Err(ProductError::Unavailable) => {
            (503, json!({"detail": "reference database unavailable"}))
        }
        Err(ProductError::Internal) => (500, json!({"detail": "unexpected server error"})),
    }
}

pub(crate) fn resolve(
    canonical_db: Option<&Path>,
    product_ref: &str,
) -> Result<Value, ProductError> {
    let product_ref = product_ref.trim();
    if product_ref.is_empty() {
        return Err(ProductError::BadRequest(
            "product_ref is required".to_owned(),
        ));
    }
    let con = open(canonical_db)?;
    let row = con
        .query_row(
            "SELECT item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                    permit_date,cancel_date,cancel_name,permit_status
             FROM products WHERE item_seq=?",
            [product_ref],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, Option<String>>(6)?,
                    row.get::<_, Option<String>>(7)?,
                    row.get::<_, String>(8)?,
                ))
            },
        )
        .optional()
        .map_err(|_| ProductError::Internal)?
        .ok_or_else(|| ProductError::NotFound("product not found".to_owned()))?;
    let (
        item_seq,
        product_name,
        manufacturer,
        ingredient_text,
        dosage_form,
        permit_date,
        cancel_date,
        cancel_name,
        permit_status,
    ) = row;

    let dosage_forms = canonical_dosage_forms(&con, &item_seq, dosage_form.as_deref())?;
    let edi_codes = edi_codes(&con, &item_seq)?;
    let linked = linked_categories(&con, &item_seq)?;
    let issues = resolution_issue_counts(&con, &item_seq)?;
    let coverage_status = if !issues.is_empty() {
        "partial"
    } else if !linked.is_empty() {
        "complete"
    } else {
        "limited"
    };
    let flags = product_flags(&con, &item_seq)?;
    let suggested_route = infer_administration_route(&dosage_forms);

    Ok(json!({
        "product_ref": item_seq,
        "catalog_item_seq": item_seq,
        "product_code": item_seq,
        "edi_codes": edi_codes,
        "matched_product_codes": [item_seq],
        "product_mapping_status": "matched",
        "product_mapping_method": "item_seq_exact",
        "product_identity_status": "matched",
        "product_identity_method": "item_seq_exact",
        "bridge_product_codes": [],
        "product_flags": flags,
        "product_name": product_name,
        "ingredient_code": null,
        "ingredient_name": ingredient_text,
        "safety_ingredients": [],
        "ingredient_mapping_status": "not_required",
        "ingredient_mapping_method": "canonical_applicability",
        "ingredient_mapping_reason": null,
        "unmapped_ingredients": [],
        "manufacturer": manufacturer,
        "dosage_form": dosage_form,
        "canonical_dosage_forms": dosage_forms,
        "suggested_administration_route": suggested_route,
        "permit_date": permit_date,
        "cancel_date": cancel_date,
        "permit_status_name": cancel_name,
        "permit_status": permit_status,
        "catalog_source": "canonical",
        "dur_match": !linked.is_empty(),
        "dur_coverage_status": coverage_status,
        "canonical_linked_categories": linked,
        "canonical_resolution_issues": issues,
    }))
}

fn open(canonical_db: Option<&Path>) -> Result<Connection, ProductError> {
    let path = canonical_db.ok_or(ProductError::Unavailable)?;
    let con = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|_| ProductError::Unavailable)?;
    con.busy_timeout(Duration::from_secs(5))
        .map_err(|_| ProductError::Internal)?;
    con.pragma_update(None, "query_only", "ON")
        .map_err(|_| ProductError::Internal)?;
    Ok(con)
}

fn canonical_dosage_forms(
    con: &Connection,
    item_seq: &str,
    product_form: Option<&str>,
) -> Result<Vec<String>, ProductError> {
    let mut forms = BTreeSet::new();
    let mut statement = con
        .prepare(
            "SELECT dosage_form FROM product_rules WHERE item_seq=? AND dosage_form IS NOT NULL
             UNION
             SELECT dosage_form FROM product_flags WHERE item_seq=? AND dosage_form IS NOT NULL",
        )
        .map_err(|_| ProductError::Internal)?;
    let rows = statement
        .query_map([item_seq, item_seq], |row| row.get::<_, String>(0))
        .map_err(|_| ProductError::Internal)?;
    for row in rows {
        let value = row.map_err(|_| ProductError::Internal)?;
        let value = value.trim();
        if !value.is_empty() {
            forms.insert(value.to_owned());
        }
    }
    if let Some(value) = product_form
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        forms.insert(value.to_owned());
    }
    Ok(forms.into_iter().collect())
}

fn edi_codes(con: &Connection, item_seq: &str) -> Result<Vec<String>, ProductError> {
    let mut statement = con
        .prepare(
            "SELECT value FROM product_identifiers
             WHERE item_seq=? AND system='EDI' ORDER BY value",
        )
        .map_err(|_| ProductError::Internal)?;
    let rows = statement
        .query_map([item_seq], |row| row.get::<_, String>(0))
        .map_err(|_| ProductError::Internal)?;
    rows.map(|row| row.map_err(|_| ProductError::Internal))
        .collect()
}

fn linked_categories(con: &Connection, item_seq: &str) -> Result<Vec<String>, ProductError> {
    let mut categories = BTreeSet::new();
    let mut statement = con
        .prepare("SELECT DISTINCT category FROM product_rule_criteria WHERE item_seq=?")
        .map_err(|_| ProductError::Internal)?;
    let rows = statement
        .query_map([item_seq], |row| row.get::<_, Option<String>>(0))
        .map_err(|_| ProductError::Internal)?;
    for row in rows {
        if let Some(category) = row.map_err(|_| ProductError::Internal)? {
            if !category.is_empty() {
                categories.insert(category);
            }
        }
    }
    let mut direct = con
        .prepare("SELECT category,effect_name FROM product_rules WHERE item_seq=?")
        .map_err(|_| ProductError::Internal)?;
    let rows = direct
        .query_map([item_seq], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })
        .map_err(|_| ProductError::Internal)?;
    for row in rows {
        let (category, effect_name) = row.map_err(|_| ProductError::Internal)?;
        if direct_rule_is_resolved(&category, effect_name.as_deref()) {
            categories.insert(category);
        }
    }
    Ok(categories.into_iter().collect())
}

fn resolution_issue_counts(
    con: &Connection,
    item_seq: &str,
) -> Result<BTreeMap<String, usize>, ProductError> {
    let mut statement = con
        .prepare(
            "SELECT r.category,r.effect_name
             FROM product_rules r
             LEFT JOIN product_criterion_links l ON l.product_rule_id=r.id
             WHERE r.item_seq=?
             GROUP BY r.id
             HAVING COUNT(l.criterion_rule_id)=0
             ORDER BY r.category,r.source_dataset_key,r.source_row",
        )
        .map_err(|_| ProductError::Internal)?;
    let rows = statement
        .query_map([item_seq], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
        })
        .map_err(|_| ProductError::Internal)?;
    let mut issues = BTreeMap::new();
    for row in rows {
        let (category, effect_name) = row.map_err(|_| ProductError::Internal)?;
        if direct_rule_is_resolved(&category, effect_name.as_deref()) {
            continue;
        }
        *issues.entry(category).or_insert(0) += 1;
    }
    Ok(issues)
}

fn direct_rule_is_resolved(category: &str, effect_name: Option<&str>) -> bool {
    category == "elderly_caution"
        || (category == "therapeutic_duplication_caution"
            && effect_name.is_some_and(|value| !value.trim().is_empty()))
}

fn product_flags(con: &Connection, item_seq: &str) -> Result<Vec<Value>, ProductError> {
    let mut statement = con
        .prepare(
            "SELECT item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,
                    details,change_date,source_dataset_key,source_row
             FROM product_flags WHERE item_seq=? ORDER BY category,source_row,flag_ordinal",
        )
        .map_err(|_| ProductError::Internal)?;
    let rows = statement
        .query_map([item_seq], |row| {
            let mut value = Map::new();
            value.insert("item_seq".to_owned(), json!(row.get::<_, String>(0)?));
            value.insert("category".to_owned(), json!(row.get::<_, String>(1)?));
            value.insert("flag_code".to_owned(), json!(row.get::<_, String>(2)?));
            value.insert("flag_name".to_owned(), json!(row.get::<_, String>(3)?));
            value.insert(
                "ingredient_name".to_owned(),
                json!(row.get::<_, Option<String>>(4)?),
            );
            value.insert(
                "dosage_form".to_owned(),
                json!(row.get::<_, Option<String>>(5)?),
            );
            value.insert(
                "details".to_owned(),
                json!(row.get::<_, Option<String>>(6)?),
            );
            value.insert(
                "change_date".to_owned(),
                json!(row.get::<_, Option<String>>(7)?),
            );
            value.insert(
                "source_dataset_key".to_owned(),
                json!(row.get::<_, String>(8)?),
            );
            value.insert("source_row".to_owned(), json!(row.get::<_, i64>(9)?));
            Ok(Value::Object(value))
        })
        .map_err(|_| ProductError::Internal)?;
    rows.map(|row| row.map_err(|_| ProductError::Internal))
        .collect()
}

fn infer_administration_route(forms: &[String]) -> &'static str {
    let mut resolved = BTreeSet::new();
    let mut saw_form = false;
    for form in forms {
        let text = form.trim();
        if text.is_empty() {
            continue;
        }
        saw_form = true;
        let Some(route) = route_for_form(text) else {
            return "unknown";
        };
        resolved.insert(route);
    }
    if saw_form && resolved.len() == 1 {
        resolved.into_iter().next().unwrap_or("unknown")
    } else {
        "unknown"
    }
}

fn route_for_form(text: &str) -> Option<&'static str> {
    if text.contains("주사") || text.contains("수액") {
        return Some("injection");
    }
    if text.contains("점안") {
        return Some("ophthalmic");
    }
    if text.contains("점이") {
        return Some("otic");
    }
    if text.contains("점비") {
        return Some("nasal");
    }
    if text.contains("흡입") {
        return Some("inhaled");
    }
    if contains_any(
        text,
        &[
            "크림",
            "연고",
            "로션",
            "겔",
            "피부액",
            "외용액",
            "경피흡수",
            "첩부",
            "카타플라스마",
        ],
    ) {
        return Some("topical");
    }
    if is_oral_form(text) {
        return Some("oral");
    }
    None
}

fn is_oral_form(text: &str) -> bool {
    if contains_any(
        text,
        &[
            "필름코팅정",
            "나정",
            "서방정",
            "장용정",
            "구강붕해정",
            "다층정",
            "저작정",
            "츄어블정",
            "당의정",
            "발포정",
            "캡슐",
            "시럽",
            "과립",
            "세립",
        ],
    ) || has_standalone_tablet(text)
    {
        return true;
    }
    if text.contains("산제") && !contains_any(text, &["외용", "피부", "주사"]) {
        return true;
    }
    if text.contains("액제")
        && !contains_any(text, &["점안", "점이", "점비", "주사", "피부", "외용"])
    {
        return true;
    }
    text.contains("경구")
}

fn has_standalone_tablet(text: &str) -> bool {
    text.match_indices("정제").any(|(index, marker)| {
        let before = &text[..index];
        let after = &text[index + marker.len()..];
        let left_ok = before
            .chars()
            .next_back()
            .is_none_or(|value| value == ',' || value == '(' || value.is_whitespace());
        let right_ok = after
            .chars()
            .next()
            .is_none_or(|value| value == ',' || value == ')' || value.is_whitespace());
        left_ok && right_ok
    })
}

fn contains_any(text: &str, markers: &[&str]) -> bool {
    markers.iter().any(|marker| text.contains(marker))
}
