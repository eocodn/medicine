use serde_json::{json, Map, Value};

use crate::safety_time::courses_overlap;

#[derive(Eq, PartialEq)]
struct RegimenSignature {
    dose: String,
    dose_unit: String,
    frequency_per_day: Option<i64>,
    as_needed: bool,
    prn_max_per_day: Option<i64>,
    schedules: Vec<String>,
    meal_relation: String,
    administration_route: String,
}

pub(crate) fn duplicate_review_items(
    current: &[Map<String, Value>],
    product: &Value,
    draft: &Map<String, Value>,
) -> Result<Vec<Value>, ()> {
    let Some(target) = item_seq(product.as_object().ok_or(())?) else {
        return Ok(Vec::new());
    };
    let target_signature = signature(draft)?;
    let mut items = Vec::new();
    for medication in current {
        if item_seq(medication) != Some(target)
            || courses_overlap(medication, draft) == Some(false)
            || signature(medication)? != target_signature
        {
            continue;
        }
        let name = medication
            .get("product_name")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .unwrap_or("같은 약");
        items.push(json!({
            "type": "duplicate_regimen",
            "severity": "warning",
            "title": "같은 복용 처방이 이미 등록되어 있어요",
            "details": format!("{name}의 복용량·횟수·시간이 겹칩니다. 별도 처방이 맞는지 확인해주세요."),
            "related_medication_id": medication.get("id").cloned().unwrap_or(Value::Null),
        }));
    }
    Ok(items)
}

fn signature(value: &Map<String, Value>) -> Result<RegimenSignature, ()> {
    let dose = if let Some(amount) = value.get("dose_amount").filter(|value| !value.is_null()) {
        normalized_number(amount)?
    } else {
        value
            .get("dosage_text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    let mut schedules = if let Some(values) = value.get("schedule_times").and_then(Value::as_array)
    {
        values
            .iter()
            .map(|value| value.as_str().map(str::to_owned).ok_or(()))
            .collect::<Result<Vec<_>, _>>()?
    } else {
        value
            .get("schedules")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|value| value.get("time_of_day").and_then(Value::as_str))
            .map(str::to_owned)
            .collect()
    };
    schedules.sort();
    Ok(RegimenSignature {
        dose,
        dose_unit: text(value, "dose_unit").unwrap_or("").to_owned(),
        frequency_per_day: integer(value.get("frequency_per_day"))?,
        as_needed: value
            .get("as_needed")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        prn_max_per_day: integer(value.get("prn_max_per_day"))?,
        schedules,
        meal_relation: text(value, "meal_relation")
            .unwrap_or("unspecified")
            .to_owned(),
        administration_route: text(value, "administration_route")
            .unwrap_or("unknown")
            .to_owned(),
    })
}

fn normalized_number(value: &Value) -> Result<String, ()> {
    let number = match value {
        Value::Number(value) => value.as_f64().ok_or(())?,
        Value::String(value) => value.parse::<f64>().map_err(|_| ())?,
        _ => return Err(()),
    };
    if !number.is_finite() {
        return Err(());
    }
    Ok(format!("{number:.15}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_owned())
}

fn integer(value: Option<&Value>) -> Result<Option<i64>, ()> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(value)) => value.as_i64().map(Some).ok_or(()),
        Some(Value::String(value)) => value.parse::<i64>().map(Some).map_err(|_| ()),
        _ => Err(()),
    }
}

fn item_seq(value: &Map<String, Value>) -> Option<&str> {
    ["catalog_item_seq", "product_ref", "product_code"]
        .into_iter()
        .find_map(|key| text(value, key).filter(|value| !value.trim().is_empty()))
}

fn text<'a>(value: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}
