use regex::Regex;
use serde_json::{Map, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

const COUNT_UNITS: [&str; 4] = ["정", "캡슐", "캡", "포"];
const AMBIGUOUS_MARKERS: [&str; 23] = [
    ",",
    "，",
    "/",
    "~",
    "또는",
    "or",
    "상이",
    "조건",
    "환자별",
    "필요시",
    "범위",
    "성인",
    "소아",
    "신장",
    "간장애",
    "체중",
    "경우",
    "일때",
    "일 때",
    "이상",
    "이하",
    "미만",
    "초과",
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct DecimalValue {
    coefficient: u128,
    scale: u32,
}

impl DecimalValue {
    fn parse(raw: &str) -> Option<Self> {
        let raw = raw.trim();
        if raw.is_empty() {
            return None;
        }
        let unsigned = raw.strip_prefix('+').unwrap_or(raw);
        if unsigned.starts_with('-') || unsigned.is_empty() {
            return None;
        }
        let mut exponent_parts = unsigned.split(['e', 'E']);
        let mantissa = exponent_parts.next()?;
        let exponent = match exponent_parts.next() {
            Some(value) => value.parse::<i32>().ok()?,
            None => 0,
        };
        if exponent_parts.next().is_some() || exponent.unsigned_abs() > 10_000 {
            return None;
        }
        let mut decimal_parts = mantissa.split('.');
        let integer = decimal_parts.next()?;
        let fraction = decimal_parts.next().unwrap_or("");
        if decimal_parts.next().is_some()
            || (integer.is_empty() && fraction.is_empty())
            || !integer.bytes().all(|byte| byte.is_ascii_digit())
            || !fraction.bytes().all(|byte| byte.is_ascii_digit())
        {
            return None;
        }
        let digits = format!("{integer}{fraction}");
        let digits = digits.trim_start_matches('0');
        if digits.is_empty() {
            return None;
        }
        let mut coefficient = digits.parse::<u128>().ok()?;
        let scale = i32::try_from(fraction.len()).ok()?.checked_sub(exponent)?;
        let mut scale = if scale <= 0 {
            coefficient = coefficient.checked_mul(pow10(u32::try_from(-scale).ok()?)?)?;
            0
        } else {
            u32::try_from(scale).ok()?
        };
        while scale > 0 && coefficient % 10 == 0 {
            coefficient /= 10;
            scale -= 1;
        }
        Some(Self { coefficient, scale })
    }

    pub(crate) fn checked_mul_u32(self, value: u32) -> Option<Self> {
        Some(
            Self {
                coefficient: self.coefficient.checked_mul(u128::from(value))?,
                scale: self.scale,
            }
            .normalized(),
        )
    }

    pub(crate) fn numeric_cmp(self, other: Self) -> Option<Ordering> {
        if self.scale == other.scale {
            return Some(self.coefficient.cmp(&other.coefficient));
        }
        if self.scale < other.scale {
            let factor = pow10(other.scale - self.scale)?;
            return Some(
                self.coefficient
                    .checked_mul(factor)?
                    .cmp(&other.coefficient),
            );
        }
        let factor = pow10(self.scale - other.scale)?;
        Some(
            self.coefficient
                .cmp(&other.coefficient.checked_mul(factor)?),
        )
    }

    pub(crate) fn to_json_number(self) -> Option<Value> {
        let value = self.canonical_string().parse::<f64>().ok()?;
        serde_json::Number::from_f64(value).map(Value::Number)
    }

    fn to_mg(self, unit: &str) -> Option<Self> {
        match unit {
            "mg" => Some(self),
            "mcg" | "μg" | "ug" => Some(
                Self {
                    coefficient: self.coefficient,
                    scale: self.scale.checked_add(3)?,
                }
                .normalized(),
            ),
            "g" => Some(
                Self {
                    coefficient: self.coefficient.checked_mul(1000)?,
                    scale: self.scale,
                }
                .normalized(),
            ),
            _ => None,
        }
    }

    fn canonical_string(self) -> String {
        if self.scale == 0 {
            return self.coefficient.to_string();
        }
        let digits = self.coefficient.to_string();
        let scale = self.scale as usize;
        if digits.len() > scale {
            let split = digits.len() - scale;
            format!("{}.{}", &digits[..split], &digits[split..])
        } else {
            format!("0.{}{}", "0".repeat(scale - digits.len()), digits)
        }
    }

    fn normalized(mut self) -> Self {
        while self.scale > 0 && self.coefficient % 10 == 0 {
            self.coefficient /= 10;
            self.scale -= 1;
        }
        self
    }
}

pub(crate) fn source_quantity(
    rows: &[Map<String, Value>],
) -> (Option<(DecimalValue, String)>, Option<String>) {
    if rows.is_empty() {
        return (None, Some("dose rule is missing".to_owned()));
    }
    let mut quantities: BTreeMap<(String, String), DecimalValue> = BTreeMap::new();
    let mut reasons = BTreeSet::new();
    for row in rows {
        if text(row, "dose_parse_status") != Some("parsed") {
            reasons.insert(
                text(row, "dose_parse_reason")
                    .unwrap_or("canonical dose criterion is not quantitatively evaluable")
                    .to_owned(),
            );
            continue;
        }
        let amount = row.get("maximum_daily_amount").and_then(decimal_value);
        let unit = text(row, "maximum_daily_unit")
            .unwrap_or("")
            .trim()
            .to_lowercase();
        let Some(amount) = amount else {
            reasons
                .insert("canonical dose criterion has an invalid structured threshold".to_owned());
            continue;
        };
        if !is_mass_unit(&unit) && !is_count_unit(&unit) {
            reasons
                .insert("canonical dose criterion has an invalid structured threshold".to_owned());
            continue;
        }
        let normalized_unit = normalize_count_unit(&unit).to_owned();
        quantities.insert((amount.canonical_string(), normalized_unit.clone()), amount);
    }
    if !reasons.is_empty() {
        if quantities.is_empty() && reasons.len() == 1 {
            return (None, reasons.into_iter().next());
        }
        return (
            None,
            Some("dose criteria do not resolve to one unambiguous daily threshold".to_owned()),
        );
    }
    if quantities.len() != 1 {
        return (
            None,
            Some("dose criteria do not resolve to one unambiguous daily threshold".to_owned()),
        );
    }
    let ((_, unit), amount) = quantities.into_iter().next().expect("one quantity");
    (Some((amount, unit)), None)
}

pub(crate) fn draft_quantity(
    draft: &Map<String, Value>,
    dosage_form: &str,
) -> (Option<(DecimalValue, String)>, Option<String>) {
    let mut amount = draft.get("dose_amount").and_then(decimal_value);
    let mut unit = draft
        .get("dose_unit")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_lowercase();
    if amount.is_none() {
        if let Some(text) = draft.get("dosage_text").and_then(Value::as_str) {
            if let Some((parsed_amount, parsed_unit)) = quantity(text) {
                amount = Some(parsed_amount);
                unit = parsed_unit.unwrap_or_default();
            }
        }
    }
    let Some(amount) = amount else {
        return unsupported_dose_input();
    };
    if is_count_unit(&unit) {
        if !countable_form(&unit, dosage_form) {
            return (
                None,
                Some("count dose requires a corresponding countable dosage form".to_owned()),
            );
        }
        return (Some((amount, normalize_count_unit(&unit).to_owned())), None);
    }
    let Some(amount) = amount.to_mg(&unit) else {
        return unsupported_dose_input();
    };
    (Some((amount, "mg".to_owned())), None)
}

pub(crate) fn frequency(draft: &Map<String, Value>) -> (Option<u32>, Option<String>) {
    let mut value = draft
        .get("frequency_per_day")
        .filter(|value| !value.is_null());
    let inferred;
    let as_needed = draft
        .get("as_needed")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    if value.is_none() && !as_needed {
        if let Some(schedules) = draft.get("schedule_times").and_then(Value::as_array) {
            if !schedules.is_empty() {
                inferred = Value::from(schedules.len() as u64);
                value = Some(&inferred);
            }
        }
    }
    let Some(decimal) = value.and_then(decimal_value) else {
        return (
            None,
            Some("daily frequency is missing (PRN without frequency is not evaluable)".to_owned()),
        );
    };
    if decimal.scale != 0 || decimal.coefficient > u128::from(u32::MAX) {
        return (
            None,
            Some("daily frequency must be a positive integer".to_owned()),
        );
    }
    (Some(decimal.coefficient as u32), None)
}

pub(crate) fn is_count_unit(unit: &str) -> bool {
    COUNT_UNITS.contains(&unit)
}

fn quantity(text: &str) -> Option<(DecimalValue, Option<String>)> {
    let normalized = remove_grouping_commas(text.trim());
    let lowered = normalized.to_lowercase();
    if normalized.is_empty()
        || AMBIGUOUS_MARKERS
            .iter()
            .any(|marker| lowered.contains(marker))
    {
        return None;
    }
    let captures = quantity_regex()
        .captures_iter(&normalized)
        .collect::<Vec<_>>();
    if captures.len() != 1 {
        return None;
    }
    let capture = &captures[0];
    let whole = capture.get(0)?;
    if whole.start() > 0 {
        let previous = normalized[..whole.start()].chars().next_back()?;
        if previous.is_ascii_digit() || previous == '.' {
            return None;
        }
    }
    let trailing = normalized[whole.end()..].trim_start();
    if trailing.chars().next().is_some_and(|value| {
        value.is_ascii_alphabetic() || ('가'..='힣').contains(&value) || value == 'μ'
    }) {
        return None;
    }
    if numeric_token_regex().find_iter(&normalized).count() != 1 {
        return None;
    }
    let amount = DecimalValue::parse(capture.get(1)?.as_str())?;
    let unit = capture
        .get(2)
        .map(|value| value.as_str().to_lowercase())
        .filter(|value| !value.is_empty());
    match unit.as_deref() {
        Some(unit) if is_mass_unit(unit) => Some((amount.to_mg(unit)?, Some("mg".to_owned()))),
        Some(unit) if is_count_unit(unit) => {
            Some((amount, Some(normalize_count_unit(unit).to_owned())))
        }
        None => Some((amount, None)),
        _ => None,
    }
}

fn unsupported_dose_input() -> (Option<(DecimalValue, String)>, Option<String>) {
    (
        None,
        Some("dose input is missing or has an unsupported unit".to_owned()),
    )
}

fn decimal_value(value: &Value) -> Option<DecimalValue> {
    match value {
        Value::String(value) => DecimalValue::parse(value),
        Value::Number(value) => DecimalValue::parse(&value.to_string()),
        _ => None,
    }
}

fn pow10(exponent: u32) -> Option<u128> {
    let mut result = 1_u128;
    for _ in 0..exponent {
        result = result.checked_mul(10)?;
    }
    Some(result)
}

fn is_mass_unit(unit: &str) -> bool {
    matches!(unit, "mcg" | "μg" | "ug" | "mg" | "g")
}

fn normalize_count_unit(unit: &str) -> &str {
    if unit == "캡" {
        "캡슐"
    } else {
        unit
    }
}

fn countable_form(unit: &str, dosage_form: &str) -> bool {
    match unit {
        "정" => dosage_form.contains('정'),
        "캡슐" | "캡" => dosage_form.contains('캡'),
        "포" => {
            dosage_form.contains('포')
                || dosage_form.contains("산제")
                || dosage_form.contains("과립")
        }
        _ => false,
    }
}

fn remove_grouping_commas(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut result = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b','
            && index > 0
            && bytes[index - 1].is_ascii_digit()
            && index + 3 < bytes.len()
            && bytes[index + 1..=index + 3].iter().all(u8::is_ascii_digit)
            && (index + 4 == bytes.len() || !bytes[index + 4].is_ascii_digit())
        {
            index += 1;
            continue;
        }
        result.push(bytes[index]);
        index += 1;
    }
    String::from_utf8(result).unwrap_or_else(|_| text.to_owned())
}

fn text<'a>(row: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    row.get(key).and_then(Value::as_str)
}

fn quantity_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(mcg|μg|ug|mg|g|정|캡슐|캡|포)?")
            .expect("quantity regex")
    })
}

fn numeric_token_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"\d+(?:\.\d+)?").expect("numeric token regex"))
}
