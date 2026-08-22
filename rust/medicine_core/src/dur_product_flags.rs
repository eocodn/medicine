use chrono::NaiveDate;
use serde_json::{Map, Value};
use std::collections::BTreeSet;

use crate::dur_display_support::{item, nonempty_text, text};
use crate::safety_time::age_years;

const SUPPORTED_FALLBACKS: [&str; 5] = [
    "age_contraindication",
    "pregnancy_contraindication",
    "dose_caution",
    "duration_caution",
    "elderly_caution",
];

pub(crate) fn build_product_flag_checks(product: &Map<String, Value>) -> Vec<Value> {
    let mut checks = Vec::new();
    for flag in product_flags(product) {
        if text(flag, "category") != Some("split_caution") {
            continue;
        }
        let details = nonempty_text(flag, "details").unwrap_or("분할 시 주의가 필요한 제품입니다.");
        let compact = details.split_whitespace().collect::<String>();
        if compact == "분할가능" {
            continue;
        }
        checks.push(item(
            "split_caution",
            "서방정 분할주의",
            "hit",
            if compact == "분할불가" {
                "분할불가"
            } else {
                "분할주의 있음"
            },
            Some(details),
            Vec::new(),
            Vec::new(),
        ));
    }
    checks
}

pub(crate) fn apply_product_flag_fallbacks(
    checks: &mut [Value],
    product: &Map<String, Value>,
    person: &Map<String, Value>,
    detailed_product_categories: &BTreeSet<String>,
    as_of: Option<NaiveDate>,
) -> Result<(), ()> {
    let mut flags = std::collections::BTreeMap::new();
    for flag in product_flags(product) {
        flags.insert(text(flag, "category").unwrap_or("").to_owned(), flag);
    }
    for category in SUPPORTED_FALLBACKS {
        if !flags.contains_key(category) || detailed_product_categories.contains(category) {
            continue;
        }
        let Some(item) = checks
            .iter_mut()
            .find(|item| item.get("category").and_then(Value::as_str) == Some(category))
        else {
            continue;
        };
        let Some(item) = item.as_object_mut() else {
            return Err(());
        };
        if text(item, "status") == Some("hit") {
            continue;
        }
        match category {
            "pregnancy_contraindication" => pregnancy_fallback(item, person),
            "elderly_caution" => elderly_fallback(item, person, as_of)?,
            "age_contraindication" => {
                update(
                    item,
                    "unknown",
                    "특정연령대금기 확인 필요",
                    Some("식약처 DUR 품목정보에 특정연령대금기 표시가 있으나 적용 연령 상세 기준을 연결하지 못했습니다."),
                );
            }
            "dose_caution" | "duration_caution" => {
                let label = if category == "dose_caution" {
                    "용량주의"
                } else {
                    "투여기간주의"
                };
                update_owned(
                    item,
                    "unknown",
                    format!("{label} 확인 필요"),
                    Some(format!(
                        "식약처 DUR 품목정보에 {label} 표시가 있으나 자동 비교에 필요한 상세 기준을 연결하지 못했습니다."
                    )),
                );
            }
            _ => {}
        }
    }
    Ok(())
}

fn pregnancy_fallback(item: &mut Map<String, Value>, person: &Map<String, Value>) {
    let pregnancy = text(person, "pregnancy_status");
    if text(person, "sex") == Some("male")
        || matches!(pregnancy, Some("not_pregnant" | "not_applicable"))
    {
        update(item, "not_applicable", "해당사항 없음", None);
    } else if pregnancy == Some("pregnant") {
        update(
            item,
            "hit",
            "임부금기 주의사항 있음",
            Some("식약처 DUR 품목정보에서 임부금기 대상 품목으로 분류된 제품입니다."),
        );
    } else {
        update(
            item,
            "unknown",
            "임신 여부 확인 필요",
            Some("식약처 DUR 품목정보에서 임부금기 대상 품목으로 분류되어 임신 여부 확인이 필요합니다."),
        );
    }
}

fn elderly_fallback(
    item: &mut Map<String, Value>,
    person: &Map<String, Value>,
    as_of: Option<NaiveDate>,
) -> Result<(), ()> {
    let birth_date = text(person, "birth_date").ok_or(())?;
    if age_years(birth_date, as_of)? < 65 {
        update(item, "not_applicable", "해당사항 없음", None);
    } else {
        update(
            item,
            "hit",
            "노인주의 대상",
            Some("식약처 DUR 품목정보에서 노인주의 대상 품목으로 분류된 제품입니다."),
        );
    }
    Ok(())
}

fn update(item: &mut Map<String, Value>, status: &str, summary: &str, details: Option<&str>) {
    update_owned(item, status, summary.to_owned(), details.map(str::to_owned));
}

fn update_owned(
    item: &mut Map<String, Value>,
    status: &str,
    summary: String,
    details: Option<String>,
) {
    item.insert("status".to_owned(), Value::String(status.to_owned()));
    item.insert("summary".to_owned(), Value::String(summary));
    if let Some(details) = details {
        item.insert("details".to_owned(), Value::String(details));
    } else {
        item.remove("details");
    }
}

fn product_flags(product: &Map<String, Value>) -> Vec<&Map<String, Value>> {
    product
        .get("product_flags")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .collect()
}
