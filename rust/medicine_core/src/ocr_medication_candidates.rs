use crate::canonical_products;
use regex::Regex;
use rusqlite::Connection;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::Path;
use std::sync::OnceLock;
use unicode_normalization::UnicodeNormalization;

const ROUTE: &str = "/api/products/ocr-candidates";
const MAX_QUERIES: usize = 96;
const MAX_QUERY_BYTES: usize = 512;
const MAX_NODE_IDS: usize = 3;
const MIN_IDENTITY_CHARS: usize = 4;

#[derive(Clone)]
struct Query {
    query_id: String,
    text: String,
    node_ids: Vec<String>,
}

#[derive(Clone)]
struct ParsedText {
    compact: String,
    numbers: Vec<String>,
    units: Vec<String>,
}

#[derive(Clone)]
struct CatalogEntry {
    item_seq: String,
    product_name: String,
    parsed: ParsedText,
}

#[derive(Clone)]
struct Candidate<'a> {
    entry: &'a CatalogEntry,
    fuzzy: bool,
    prefix: bool,
    extra_characters: usize,
}

pub(crate) fn handles_request(method: &str, path: &str) -> bool {
    method.trim().eq_ignore_ascii_case("POST") && path == ROUTE
}

pub(crate) fn handle_request(
    canonical_db: Option<&Path>,
    method: &str,
    path: &str,
    body_json: &str,
) -> Option<(u16, Value)> {
    if !handles_request(method, path) {
        return None;
    }
    let queries = match parse_queries(body_json) {
        Ok(value) => value,
        Err(detail) => return Some((400, json!({"detail": detail}))),
    };
    let connection = match canonical_products::open(canonical_db) {
        Ok(value) => value,
        Err(_) => {
            return Some((
                503,
                json!({"detail": "product search index unavailable; app update required"}),
            ));
        }
    };
    match discover_rows(&connection, &queries) {
        Ok(rows) => Some((200, json!({"rows": rows}))),
        Err(_) => Some((500, json!({"detail": "unexpected server error"}))),
    }
}

fn parse_queries(body_json: &str) -> Result<Vec<Query>, String> {
    let body: Value = serde_json::from_str(body_json)
        .map_err(|_| "OCR candidate request must be valid JSON".to_owned())?;
    let raw_queries = body
        .get("queries")
        .and_then(Value::as_array)
        .ok_or_else(|| "queries must be an array".to_owned())?;
    if raw_queries.len() > MAX_QUERIES {
        return Err(format!("queries must contain at most {MAX_QUERIES} items"));
    }
    let mut seen = HashSet::new();
    let mut queries = Vec::with_capacity(raw_queries.len());
    for raw in raw_queries {
        let object = raw
            .as_object()
            .ok_or_else(|| "each OCR candidate query must be an object".to_owned())?;
        let query_id = object
            .get("query_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| valid_identifier(value))
            .ok_or_else(|| "query_id must be a safe identifier".to_owned())?;
        if !seen.insert(query_id.to_owned()) {
            return Err("query_id values must be unique".to_owned());
        }
        let text = object
            .get("text")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty() && value.len() <= MAX_QUERY_BYTES)
            .ok_or_else(|| "query text must be non-empty and bounded".to_owned())?;
        let node_ids = match object.get("node_ids") {
            None => vec![query_id.to_owned()],
            Some(Value::Array(values)) if (1..=MAX_NODE_IDS).contains(&values.len()) => {
                let mut nodes = Vec::with_capacity(values.len());
                let mut local = HashSet::new();
                for value in values {
                    let node_id = value
                        .as_str()
                        .map(str::trim)
                        .filter(|item| valid_identifier(item))
                        .ok_or_else(|| "node_ids must contain safe identifiers".to_owned())?;
                    if !local.insert(node_id.to_owned()) {
                        return Err("node_ids must be unique within a query".to_owned());
                    }
                    nodes.push(node_id.to_owned());
                }
                nodes
            }
            _ => return Err("node_ids must contain one to three identifiers".to_owned()),
        };
        queries.push(Query {
            query_id: query_id.to_owned(),
            text: text.to_owned(),
            node_ids,
        });
    }
    Ok(queries)
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

fn discover_rows(connection: &Connection, queries: &[Query]) -> rusqlite::Result<Vec<Value>> {
    let mut statement = connection.prepare(
        "SELECT item_seq, product_name FROM products WHERE permit_status='active' ORDER BY item_seq",
    )?;
    let catalog = statement
        .query_map([], |row| {
            let item_seq = row.get::<_, String>(0)?;
            let product_name = row.get::<_, String>(1)?;
            Ok(CatalogEntry {
                parsed: parse_text(&product_name, false),
                item_seq,
                product_name,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;

    let mut rows = Vec::new();
    let mut consumed_nodes = HashSet::new();
    for query in queries {
        if query.node_ids.iter().any(|node| consumed_nodes.contains(node)) {
            continue;
        }
        let parsed = parse_text(&query.text, true);
        if parsed.compact.chars().count() < MIN_IDENTITY_CHARS {
            continue;
        }
        let mut candidates = catalog
            .iter()
            .filter_map(|entry| match_candidate(&parsed, entry))
            .collect::<Vec<_>>();
        candidates.sort_by(|left, right| {
            candidate_rank(left)
                .cmp(&candidate_rank(right))
                .then_with(|| left.entry.item_seq.cmp(&right.entry.item_seq))
        });
        let Some(best) = candidates.first() else {
            continue;
        };
        let ambiguous_top = candidates
            .get(1)
            .is_some_and(|second| candidate_rank(best) == candidate_rank(second));
        let product_query = if ambiguous_top {
            parsed.compact.clone()
        } else {
            best.entry.product_name.clone()
        };
        rows.push(json!({
            "row_id": query.query_id,
            "product_query": product_query,
        }));
        consumed_nodes.extend(query.node_ids.iter().cloned());
    }
    Ok(rows)
}

fn candidate_rank(candidate: &Candidate<'_>) -> (u8, u8, usize) {
    (
        u8::from(candidate.fuzzy),
        u8::from(!candidate.prefix),
        candidate.extra_characters,
    )
}

fn match_candidate<'a>(query: &ParsedText, entry: &'a CatalogEntry) -> Option<Candidate<'a>> {
    if !ordered_subsequence(&query.numbers, &entry.parsed.numbers)
        || !ordered_subsequence(&query.units, &entry.parsed.units)
    {
        return None;
    }
    let (matched, prefix) = match_compact(&query.compact, &entry.parsed.compact);
    if !matched {
        return None;
    }
    Some(Candidate {
        entry,
        fuzzy: !entry.parsed.compact.contains(&query.compact),
        prefix,
        extra_characters: entry
            .parsed
            .compact
            .chars()
            .count()
            .saturating_sub(query.compact.chars().count()),
    })
}

fn parse_text(value: &str, ocr: bool) -> ParsedText {
    let mut normalized = canonical_text(value);
    if ocr {
        normalized = trailing_regimen_re().replace(&normalized, "$1").into_owned();
    }
    let mut text_tokens = Vec::new();
    let mut numbers = Vec::new();
    let mut units = Vec::new();
    for capture in token_re().captures_iter(&normalized) {
        let token = capture.get(0).map_or("", |item| item.as_str());
        if let Some(unit) = capture.get(1) {
            units.push(unit.as_str().to_ascii_lowercase());
        } else if token.chars().next().is_some_and(|ch| ch.is_ascii_digit()) {
            numbers.push(normalize_number(token));
        } else {
            text_tokens.push(token.to_lowercase());
        }
    }
    ParsedText {
        compact: text_tokens.concat(),
        numbers,
        units,
    }
}

fn canonical_text(value: &str) -> String {
    let mut text = value
        .nfkc()
        .flat_map(char::to_lowercase)
        .collect::<String>()
        .replace('µ', "μ");
    for (pattern, replacement) in [
        (korean_ug_re(), " __unit_ug__ "),
        (korean_mg_re(), " __unit_mg__ "),
        (korean_ml_re(), " __unit_ml__ "),
    ] {
        text = pattern.replace_all(&text, replacement).into_owned();
    }
    text = ascii_ug_re().replace_all(&text, "$1 __unit_ug__ $2").into_owned();
    text = ascii_mg_re().replace_all(&text, "$1 __unit_mg__ $2").into_owned();
    text = ascii_ml_re().replace_all(&text, "$1 __unit_ml__ $2").into_owned();
    text
}

fn normalize_number(value: &str) -> String {
    let (integer, fraction) = value.split_once('.').unwrap_or((value, ""));
    let integer = integer.trim_start_matches('0');
    let integer = if integer.is_empty() { "0" } else { integer };
    let fraction = fraction.trim_end_matches('0');
    if fraction.is_empty() {
        integer.to_owned()
    } else {
        format!("{integer}.{fraction}")
    }
}

fn ordered_subsequence(needle: &[String], haystack: &[String]) -> bool {
    if needle.is_empty() {
        return true;
    }
    let mut cursor = 0;
    for value in haystack {
        if value == &needle[cursor] {
            cursor += 1;
            if cursor == needle.len() {
                return true;
            }
        }
    }
    false
}

fn match_compact(query: &str, candidate: &str) -> (bool, bool) {
    if candidate.contains(query) {
        return (true, candidate.starts_with(query));
    }
    let query_chars = query.chars().collect::<Vec<_>>();
    let candidate_chars = candidate.chars().collect::<Vec<_>>();
    if query_chars.len() > candidate_chars.len() {
        return (false, false);
    }
    for window in candidate_chars.windows(query_chars.len()) {
        if window
            .iter()
            .zip(&query_chars)
            .filter(|(left, right)| left != right)
            .count()
            <= 1
        {
            return (true, false);
        }
    }
    (false, false)
}

fn token_re() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| {
        Regex::new(r"__unit_(mg|ug|ml)__|\d+(?:\.\d+)?|[a-z]+|[가-힣]+")
            .expect("valid OCR token regex")
    })
}

fn trailing_regimen_re() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| {
        Regex::new(r"(__unit_(?:mg|ug|ml)__)(?:\s*)(\d+(?:\.\d+)?)(?:\s*)(?:정|캡슐|포)\s*$")
            .expect("valid trailing regimen regex")
    })
}

macro_rules! regex_once {
    ($name:ident, $pattern:literal) => {
        fn $name() -> &'static Regex {
            static VALUE: OnceLock<Regex> = OnceLock::new();
            VALUE.get_or_init(|| Regex::new($pattern).expect("valid medication identity regex"))
        }
    };
}

regex_once!(ascii_ug_re, r"(?i)(^|[^a-z가-힣])(?:mcg|ug|μg)($|[^a-z가-힣])");
regex_once!(ascii_mg_re, r"(?i)(^|[^a-z가-힣])mg($|[^a-z가-힣])");
regex_once!(ascii_ml_re, r"(?i)(^|[^a-z가-힣])ml($|[^a-z가-힣])");
regex_once!(korean_ug_re, r"(?i)마이크로(?:그램|그람)");
regex_once!(korean_mg_re, r"(?i)밀리(?:그램|그람)");
regex_once!(korean_ml_re, r"(?i)밀리리터");
