use crate::canonical_products;
use regex::Regex;
use rusqlite::params;
use serde_json::{json, Value};
use std::path::Path;
use std::sync::OnceLock;
use unicode_normalization::UnicodeNormalization;

/// Returns whether this module owns the product-search route.
pub(crate) fn handles_request(method: &str, raw_path: &str) -> bool {
    method.trim().eq_ignore_ascii_case("GET") && request_path(raw_path) == "/api/products"
}

pub(crate) fn handle(
    canonical_db: Option<&Path>,
    method: &str,
    raw_path: &str,
) -> Option<(u16, Value)> {
    if !handles_request(method, raw_path) {
        return None;
    }

    Some(match parse_query(raw_path) {
        Ok(query) => match search(canonical_db, &query) {
            Ok(body) => (200, body),
            Err(SearchError::Unavailable) => (
                503,
                json!({"detail": "product search index unavailable; app update required"}),
            ),
            Err(SearchError::Internal) => (500, json!({"detail": "unexpected server error"})),
        },
        Err(detail) => (400, json!({"detail": detail})),
    })
}

/// Alias matching the other request modules' dispatch naming convention.
pub(crate) fn handle_request(
    canonical_db: Option<&Path>,
    method: &str,
    raw_path: &str,
) -> Option<(u16, Value)> {
    handle(canonical_db, method, raw_path)
}

fn request_path(raw_path: &str) -> &str {
    let before_query = raw_path.split_once('?').map_or(raw_path, |(path, _)| path);
    before_query
        .split_once('#')
        .map_or(before_query, |(path, _)| path)
}

fn parse_query(raw_path: &str) -> Result<ParsedSearchQuery, String> {
    let Some((_, raw_query)) = raw_path.split_once('?') else {
        return Err("q is required".to_owned());
    };
    let raw_query = raw_query
        .split_once('#')
        .map_or(raw_query, |(query, _)| query);
    let mut query = SearchQuery::default();

    for pair in raw_query.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (raw_key, raw_value) = pair.split_once('=').unwrap_or((pair, ""));
        let key = percent_decode(raw_key)?;
        let value = percent_decode(raw_value)?;
        match key.as_str() {
            "q" => query.term = Some(value),
            "limit" => query.limit = Some(value),
            "offset" => query.offset = Some(value),
            "include_inactive" => query.include_inactive = Some(value),
            "explain_matches" => query.explain_matches = Some(value),
            _ => {}
        }
    }

    let term = query
        .term
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "q is required".to_owned())?;

    let limit = match query.limit.as_deref() {
        None => 30,
        Some(value) => parse_python_limit(value)?,
    };

    let offset = match query.offset.as_deref() {
        None => 0,
        Some(value) => parse_python_offset(value)?,
    };

    let include_inactive = match query.include_inactive.as_deref() {
        None => false,
        Some(value) => parse_bool(value, "include_inactive")?,
    };

    let explain_matches = match query.explain_matches.as_deref() {
        None => false,
        Some(value) => parse_bool(value, "explain_matches")?,
    };
    let normalized = normalize_search_text(term);
    let tokens = normalized
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if tokens.is_empty() {
        return Err("q is required".to_owned());
    }

    Ok(ParsedSearchQuery {
        normalized,
        tokens,
        limit,
        offset,
        include_inactive,
        explain_matches,
    })
}

#[derive(Default)]
struct SearchQuery {
    term: Option<String>,
    limit: Option<String>,
    offset: Option<String>,
    include_inactive: Option<String>,
    explain_matches: Option<String>,
}

struct ParsedSearchQuery {
    normalized: String,
    tokens: Vec<String>,
    limit: u32,
    offset: u64,
    include_inactive: bool,
    explain_matches: bool,
}

enum SearchError {
    Unavailable,
    Internal,
}

fn normalize_search_text(value: &str) -> String {
    let normalized = value
        .nfkc()
        .flat_map(char::to_lowercase)
        .collect::<String>();
    normalized.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn parse_bool(value: &str, name: &str) -> Result<bool, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(format!("invalid boolean query parameter: {name}")),
    }
}

fn fts_anchor(tokens: &[String]) -> Option<String> {
    // FTS is only a candidate accelerator. Trigram MATCH cannot represent
    // shorter terms, so those queries deliberately scan the compact document
    // table; the lexical predicates below remain authoritative in both paths.
    tokens
        .iter()
        .filter(|token| token.chars().count() >= 3)
        .max_by_key(|token| token.chars().count())
        .map(|token| format!("\"{}\"", token.replace('"', "\"\"")))
}

fn search(canonical_db: Option<&Path>, query: &ParsedSearchQuery) -> Result<Value, SearchError> {
    let con = canonical_products::open(canonical_db).map_err(|_| SearchError::Unavailable)?;
    let search_objects = con
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master \
             WHERE type='table' AND name IN ('product_search_documents','product_search_fts')",
            [],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|_| SearchError::Internal)?;
    if search_objects != 2 {
        return Err(SearchError::Unavailable);
    }

    let tokens_json = serde_json::to_string(&query.tokens).map_err(|_| SearchError::Internal)?;
    let fts_query = fts_anchor(&query.tokens);
    let fetch_limit = i64::from(query.limit) + 1;
    let offset = i64::try_from(query.offset).map_err(|_| SearchError::Internal)?;
    let mut statement = con
        .prepare(
            r#"
            WITH matching AS (
                SELECT
                    d.item_seq,
                    CASE
                        WHEN d.normalized_product_name = ?1 THEN 0
                        WHEN instr(d.normalized_product_name, ?1) = 1 THEN 1
                        WHEN NOT EXISTS (
                            SELECT 1 FROM json_each(?2) t
                            WHERE instr(d.normalized_product_name, t.value) = 0
                        ) THEN 2
                        WHEN instr(d.normalized_ingredient_names, char(10) || ?1 || char(10)) > 0 THEN 3
                        WHEN instr(d.normalized_ingredient_names, char(10) || ?1) > 0 THEN 4
                        WHEN d.normalized_manufacturer = ?1 THEN 5
                        WHEN instr(d.normalized_manufacturer, ?1) = 1 THEN 6
                        ELSE 7
                    END AS match_tier,
                    (SELECT COUNT(*) FROM json_each(?2) t
                     WHERE instr(d.normalized_product_name, t.value) > 0) AS name_terms,
                    (SELECT COUNT(*) FROM json_each(?2) t
                     WHERE instr(d.normalized_manufacturer, t.value) > 0) AS manufacturer_terms,
                    (SELECT COUNT(*) FROM json_each(?2) t
                     WHERE instr(d.normalized_ingredient_names, t.value) > 0) AS ingredient_terms
                FROM product_search_documents d
                JOIN products status_product ON status_product.item_seq=d.item_seq
                WHERE (?3 != 0 OR status_product.permit_status='active')
                  AND (?6 IS NULL OR d.rowid IN (
                      SELECT rowid FROM product_search_fts
                      WHERE product_search_fts MATCH ?6
                  ))
                  AND NOT EXISTS (
                      SELECT 1 FROM json_each(?2) t
                      WHERE instr(d.normalized_product_name, t.value) = 0
                        AND instr(d.normalized_manufacturer, t.value) = 0
                        AND instr(d.normalized_ingredient_names, t.value) = 0
                  )
                ORDER BY match_tier, name_terms DESC, d.item_seq
                LIMIT ?4 OFFSET ?5
            )
            SELECT
                p.item_seq,p.product_name,p.manufacturer,p.ingredient_text,
                p.cancel_date,p.cancel_name,p.permit_status,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM product_rules r
                        WHERE r.item_seq=p.item_seq
                          AND NOT (
                              r.category='elderly_caution'
                              OR (r.category='therapeutic_duplication_caution'
                                  AND r.effect_name IS NOT NULL AND TRIM(r.effect_name)<>'')
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM product_criterion_links l
                              WHERE l.product_rule_id=r.id
                          )
                    ) THEN 'partial'
                    WHEN EXISTS (
                        SELECT 1 FROM product_rules r
                        WHERE r.item_seq=p.item_seq
                          AND (
                              r.category='elderly_caution'
                              OR (r.category='therapeutic_duplication_caution'
                                  AND r.effect_name IS NOT NULL AND TRIM(r.effect_name)<>'')
                              OR EXISTS (
                                  SELECT 1 FROM product_criterion_links l
                                  WHERE l.product_rule_id=r.id
                              )
                          )
                    ) THEN 'complete'
                    ELSE 'limited'
                END AS dur_coverage_status,
                m.match_tier,m.name_terms,m.manufacturer_terms,m.ingredient_terms
            FROM matching m
            JOIN products p ON p.item_seq=m.item_seq
            ORDER BY m.match_tier,m.name_terms DESC,m.item_seq
            "#,
        )
        .map_err(|_| SearchError::Internal)?;
    let rows = statement
        .query_map(
            params![
                query.normalized,
                tokens_json,
                if query.include_inactive { 1 } else { 0 },
                fetch_limit,
                offset,
                fts_query,
            ],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, Option<String>>(3)?,
                    row.get::<_, Option<String>>(4)?,
                    row.get::<_, Option<String>>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, i64>(8)?,
                    row.get::<_, i64>(9)?,
                    row.get::<_, i64>(10)?,
                    row.get::<_, i64>(11)?,
                ))
            },
        )
        .map_err(|_| SearchError::Internal)?;

    let mut items = Vec::new();
    for row in rows {
        let (
            item_seq,
            product_name,
            manufacturer,
            ingredient_text,
            cancel_date,
            cancel_name,
            permit_status,
            dur_coverage_status,
            match_tier,
            name_terms,
            manufacturer_terms,
            ingredient_terms,
        ) = row.map_err(|_| SearchError::Internal)?;
        let mut item = json!({
            "product_ref": item_seq,
            "product_name": product_name,
            "manufacturer": manufacturer,
            "ingredient_name": ingredient_text,
            "cancel_date": cancel_date,
            "permit_status_name": cancel_name,
            "permit_status": permit_status,
            "dur_coverage_status": dur_coverage_status,
        });
        if query.explain_matches {
            let mut fields = Vec::new();
            if name_terms > 0 {
                fields.push("product_name");
            }
            if ingredient_terms > 0 {
                fields.push("ingredient");
            }
            if manufacturer_terms > 0 {
                fields.push("manufacturer");
            }
            item["search_match"] = json!({
                "tier": match_tier,
                "matched_fields": fields,
            });
        }
        items.push(item);
    }

    let has_more = items.len() > query.limit as usize;
    if has_more {
        items.pop();
    }
    let next_offset = has_more.then(|| query.offset + u64::from(query.limit));
    Ok(json!({
        "items": items,
        "has_more": has_more,
        "next_offset": next_offset,
    }))
}

fn percent_decode(value: &str) -> Result<String, String> {
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => decoded.push(b' '),
            b'%' if index + 2 < bytes.len() => {
                match (hex_value(bytes[index + 1]), hex_value(bytes[index + 2])) {
                    (Some(high), Some(low)) => {
                        decoded.push((high << 4) | low);
                        index += 2;
                    }
                    _ => decoded.push(b'%'),
                }
            }
            byte => decoded.push(byte),
        }
        index += 1;
    }
    Ok(String::from_utf8_lossy(&decoded).into_owned())
}

fn parse_python_limit(value: &str) -> Result<u32, String> {
    let value = value.trim();
    let mut chars = value.chars().peekable();
    let negative = match chars.peek() {
        Some('+') => {
            chars.next();
            false
        }
        Some('-') => {
            chars.next();
            true
        }
        _ => false,
    };
    let mut digits = String::new();
    let mut previous_was_digit = false;
    while let Some(character) = chars.next() {
        if character == '_' {
            if !previous_was_digit || chars.peek().and_then(|next| decimal_digit(*next)).is_none() {
                return Err("limit must be an integer".to_owned());
            }
            previous_was_digit = false;
            continue;
        }
        let digit =
            decimal_digit(character).ok_or_else(|| "limit must be an integer".to_owned())?;
        digits.push(char::from(b'0' + digit));
        previous_was_digit = true;
    }
    if digits.is_empty() || !previous_was_digit {
        return Err("limit must be an integer".to_owned());
    }
    let significant = digits.trim_start_matches('0');
    if negative || significant.len() > 3 {
        return Err("limit must be between 1 and 100".to_owned());
    }
    let parsed = if significant.is_empty() {
        0
    } else {
        significant
            .parse::<u32>()
            .map_err(|_| "limit must be between 1 and 100".to_owned())?
    };
    if (1..=100).contains(&parsed) {
        Ok(parsed)
    } else {
        Err("limit must be between 1 and 100".to_owned())
    }
}

fn parse_python_offset(value: &str) -> Result<u64, String> {
    let value = value.trim();
    let mut chars = value.chars().peekable();
    match chars.peek() {
        Some('+') => {
            chars.next();
        }
        Some('-') => return Err("offset must be a non-negative integer".to_owned()),
        _ => {}
    }
    let mut digits = String::new();
    let mut previous_was_digit = false;
    while let Some(character) = chars.next() {
        if character == '_' {
            if !previous_was_digit || chars.peek().and_then(|next| decimal_digit(*next)).is_none() {
                return Err("offset must be a non-negative integer".to_owned());
            }
            previous_was_digit = false;
            continue;
        }
        let digit = decimal_digit(character)
            .ok_or_else(|| "offset must be a non-negative integer".to_owned())?;
        digits.push(char::from(b'0' + digit));
        previous_was_digit = true;
    }
    if digits.is_empty() || !previous_was_digit {
        return Err("offset must be a non-negative integer".to_owned());
    }
    let significant = digits.trim_start_matches('0');
    if significant.is_empty() {
        return Ok(0);
    }
    let parsed = significant
        .parse::<u64>()
        .map_err(|_| "offset must be a non-negative integer".to_owned())?;
    if parsed > i64::MAX as u64 {
        return Err("offset must be a non-negative integer".to_owned());
    }
    Ok(parsed)
}

fn decimal_digit(value: char) -> Option<u8> {
    static DECIMAL: OnceLock<Regex> = OnceLock::new();
    let decimal = DECIMAL.get_or_init(|| Regex::new(r"^\p{Nd}$").expect("valid Unicode property"));
    if !decimal.is_match(value.encode_utf8(&mut [0; 4])) {
        return None;
    }
    let codepoint = value as u32;
    let mut run_start = codepoint;
    while let Some(previous) = run_start.checked_sub(1).and_then(char::from_u32) {
        if !decimal.is_match(previous.encode_utf8(&mut [0; 4])) {
            break;
        }
        run_start -= 1;
    }
    Some(((codepoint - run_start) % 10) as u8)
}

fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}
