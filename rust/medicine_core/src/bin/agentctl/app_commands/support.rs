use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

const DEFAULT_CANONICAL_DB: &str = "data/db/mobile.sqlite";
const DEFAULT_PERSONAL_DB: &str = "data/db/personal.sqlite";

pub(super) struct AppConfig {
    pub(super) canonical_db: PathBuf,
    pub(super) personal_db: PathBuf,
}

#[derive(Default)]
pub(super) struct ParsedArgs {
    pub(super) values: HashMap<String, Vec<String>>,
    pub(super) flags: HashSet<String>,
    pub(super) positionals: Vec<String>,
}

pub(super) fn parse_global_options(
    args: &[String],
    usage: fn() -> String,
) -> Result<(AppConfig, usize), String> {
    let mut canonical_db = PathBuf::from(DEFAULT_CANONICAL_DB);
    let mut personal_db = PathBuf::from(DEFAULT_PERSONAL_DB);
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = PathBuf::from(args.get(index).ok_or_else(usage)?);
            }
            "--personal-db" => {
                index += 1;
                personal_db = PathBuf::from(args.get(index).ok_or_else(usage)?);
            }
            _ => break,
        }
        index += 1;
    }
    Ok((
        AppConfig {
            canonical_db,
            personal_db,
        },
        index,
    ))
}

pub(super) fn parse(
    args: &[String],
    value_options: &[&str],
    switches: &[&str],
    usage: fn() -> String,
) -> Result<ParsedArgs, String> {
    let value_options = value_options.iter().copied().collect::<HashSet<_>>();
    let switches = switches.iter().copied().collect::<HashSet<_>>();
    let mut parsed = ParsedArgs::default();
    let mut index = 0;
    while index < args.len() {
        let value = args[index].as_str();
        if value_options.contains(value) {
            index += 1;
            let option_value = args.get(index).ok_or_else(usage)?.to_owned();
            parsed
                .values
                .entry(value.to_owned())
                .or_default()
                .push(option_value);
        } else if switches.contains(value) {
            parsed.flags.insert(value.to_owned());
        } else if value.starts_with("--") {
            return Err(usage());
        } else {
            parsed.positionals.push(value.to_owned());
        }
        index += 1;
    }
    Ok(parsed)
}

impl ParsedArgs {
    pub(super) fn required(&self, name: &str) -> Result<&str, String> {
        self.optional(name)
            .ok_or_else(|| format!("missing required option {name}"))
    }

    pub(super) fn optional(&self, name: &str) -> Option<&str> {
        self.values
            .get(name)
            .and_then(|values| values.last())
            .map(String::as_str)
    }

    pub(super) fn one_of(&self, names: &[&str]) -> Option<&str> {
        names.iter().find_map(|name| self.optional(name))
    }

    pub(super) fn all(&self, name: &str) -> Vec<&str> {
        self.values
            .get(name)
            .map(|values| values.iter().map(String::as_str).collect())
            .unwrap_or_default()
    }

    pub(super) fn flag(&self, name: &str) -> bool {
        self.flags.contains(name)
    }

    pub(super) fn parse_i64(&self, name: &str, default: i64) -> Result<i64, String> {
        match self.optional(name) {
            Some(value) => value
                .parse::<i64>()
                .map_err(|_| format!("{name} must be an integer")),
            None => Ok(default),
        }
    }

    pub(super) fn parse_required_i64(&self, name: &str) -> Result<i64, String> {
        self.required(name)?
            .parse::<i64>()
            .map_err(|_| format!("{name} must be an integer"))
    }

    pub(super) fn expect_no_positionals(&self) -> Result<(), String> {
        if self.positionals.is_empty() {
            Ok(())
        } else {
            Err("unexpected positional argument".to_owned())
        }
    }
}

pub(super) fn emit_body(envelope: &Value, as_json: bool) -> Result<(), String> {
    let body = envelope
        .get("body")
        .ok_or_else(|| "medicine engine response is missing body".to_owned())?;
    if as_json {
        println!("{body}");
    } else if let Some(items) = body.as_array() {
        for item in items {
            println!("{item}");
        }
    } else {
        println!("{body}");
    }
    Ok(())
}

pub(super) fn exit_code(envelope: &Value) -> i32 {
    let status = envelope
        .get("status")
        .and_then(Value::as_u64)
        .unwrap_or(500);
    let body = envelope.get("body").unwrap_or(&Value::Null);
    if (200..300).contains(&status) {
        0
    } else if status == 409 && body.get("confirmation_required") == Some(&Value::Bool(true)) {
        2
    } else if status == 503
        && body.get("detail").and_then(Value::as_str)
            == Some("product search engine is not implemented")
    {
        3
    } else {
        1
    }
}

pub(super) fn insert_optional_string(
    body: &mut Map<String, Value>,
    key: &str,
    value: Option<&str>,
) {
    if let Some(value) = value {
        body.insert(key.to_owned(), Value::String(value.to_owned()));
    }
}

pub(super) fn insert_optional_i64(
    body: &mut Map<String, Value>,
    key: &str,
    value: Option<&str>,
) -> Result<(), String> {
    if let Some(value) = value {
        insert_i64_required(body, key, value)?;
    }
    Ok(())
}

pub(super) fn insert_i64_required(
    body: &mut Map<String, Value>,
    key: &str,
    value: &str,
) -> Result<(), String> {
    let value = value
        .parse::<i64>()
        .map_err(|_| format!("{key} must be an integer"))?;
    body.insert(key.to_owned(), Value::from(value));
    Ok(())
}

pub(super) fn insert_optional_f64(
    body: &mut Map<String, Value>,
    key: &str,
    value: Option<&str>,
) -> Result<(), String> {
    if let Some(value) = value {
        let value = value
            .parse::<f64>()
            .map_err(|_| format!("{key} must be a number"))?;
        body.insert(key.to_owned(), Value::from(value));
    }
    Ok(())
}

pub(super) fn query_string(values: &[(&str, String)]) -> String {
    values
        .iter()
        .map(|(key, value)| format!("{}={}", encode_query(key), encode_query(value)))
        .collect::<Vec<_>>()
        .join("&")
}

pub(super) fn encode_path(value: &str) -> String {
    percent_encode(value, false)
}

pub(super) fn encode_query(value: &str) -> String {
    percent_encode(value, true)
}

fn percent_encode(value: &str, space_as_plus: bool) -> String {
    let mut output = String::new();
    for byte in value.as_bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            output.push(char::from(*byte));
        } else if *byte == b' ' && space_as_plus {
            output.push('+');
        } else {
            output.push('%');
            output.push_str(&format!("{byte:02X}"));
        }
    }
    output
}
