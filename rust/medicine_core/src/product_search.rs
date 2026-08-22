use regex::Regex;
use serde_json::{json, Value};
use std::sync::OnceLock;

/// Returns whether this module owns the product-search route.
pub(crate) fn handles_request(method: &str, raw_path: &str) -> bool {
    method.trim().eq_ignore_ascii_case("GET") && request_path(raw_path) == "/api/products"
}

/// Handles the intentionally unavailable product-search boundary.
///
/// Reference availability is checked by the engine before dispatch. Once the
/// reference is available, malformed search inputs retain their 400 contract.
pub(crate) fn handle(method: &str, raw_path: &str) -> Option<(u16, Value)> {
    if !handles_request(method, raw_path) {
        return None;
    }

    Some(match parse_query(raw_path) {
        Ok(_) => (
            503,
            json!({"detail": "product search engine is not implemented"}),
        ),
        Err(detail) => (400, json!({"detail": detail})),
    })
}

/// Alias matching the other request modules' dispatch naming convention.
pub(crate) fn handle_request(method: &str, raw_path: &str) -> Option<(u16, Value)> {
    handle(method, raw_path)
}

fn request_path(raw_path: &str) -> &str {
    let before_query = raw_path.split_once('?').map_or(raw_path, |(path, _)| path);
    before_query
        .split_once('#')
        .map_or(before_query, |(path, _)| path)
}

fn parse_query(raw_path: &str) -> Result<(), String> {
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
            "include_inactive" => query.include_inactive = Some(value),
            _ => {}
        }
    }

    let _term = query
        .term
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "q is required".to_owned())?;

    let _limit = match query.limit.as_deref() {
        None => 30,
        Some(value) => parse_python_limit(value)?,
    };

    let _include_inactive = match query.include_inactive.as_deref() {
        None => false,
        Some(value) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => true,
            "0" | "false" | "no" | "off" => false,
            _ => return Err("invalid boolean query parameter: include_inactive".to_owned()),
        },
    };

    Ok(())
}

#[derive(Default)]
struct SearchQuery {
    term: Option<String>,
    limit: Option<String>,
    include_inactive: Option<String>,
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
