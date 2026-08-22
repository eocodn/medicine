use rusqlite::Connection;
use serde_json::{json, Map, Value};

use crate::canonical_products;
use crate::medication_records::{self, Medication, RecordError};

pub(crate) fn load_for_preview_excluding(
    personal: &Connection,
    canonical: &Connection,
    person_id: &str,
    exclude_medication_id: Option<&str>,
) -> Result<(Vec<Map<String, Value>>, usize), RecordError> {
    let medications = medication_records::load_for_person(personal, person_id, false)?;
    let medications = medications
        .into_iter()
        .filter(|medication| match exclude_medication_id {
            Some(excluded_id) => medication.id().map_or(true, |id| id != excluded_id),
            None => true,
        })
        .collect::<Vec<_>>();
    let active_count = medications
        .iter()
        .filter(|medication| medication.active())
        .count();
    let current = medications
        .into_iter()
        .map(|medication| resolve_current(canonical, medication))
        .collect::<Result<Vec<_>, _>>()?;
    Ok((current, active_count))
}

fn resolve_current(
    canonical: &Connection,
    medication: Medication,
) -> Result<Map<String, Value>, RecordError> {
    let medication_id = medication.id()?.to_owned();
    let Value::Object(mut stored) = medication.to_json() else {
        return Err(RecordError::Internal);
    };
    let reference = stored
        .get("catalog_item_seq")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned);
    let Some(reference) = reference else {
        fallback(&mut stored);
        return Ok(stored);
    };
    match canonical_products::resolve_from_connection(canonical, &reference) {
        Ok(Value::Object(product)) => {
            for (key, value) in product {
                stored.insert(key, value);
            }
            stored.insert("id".to_owned(), Value::String(medication_id));
            Ok(stored)
        }
        Ok(_) => Err(RecordError::Internal),
        Err(_) => {
            fallback(&mut stored);
            Ok(stored)
        }
    }
}

fn fallback(medication: &mut Map<String, Value>) {
    let fixed = json!({
        "safety_ingredients": [],
        "ingredient_mapping_status": "not_required",
        "ingredient_mapping_method": "canonical_applicability",
        "product_mapping_status": "not_matched",
        "product_identity_status": "not_matched",
        "product_identity_method": null,
        "matched_product_codes": [],
        "edi_codes": [],
        "canonical_resolution_issues": {},
    });
    if let Value::Object(fixed) = fixed {
        medication.extend(fixed);
    }
}
