use serde_json::{Map, Value};

use crate::dur_display_support::{item, item_owned, scalar_text, text};

pub(crate) fn quantitative_item(
    category: &str,
    label: &str,
    check: &Map<String, Value>,
    mapping_complete: bool,
    mapping_reason: &str,
    dataset_verified: bool,
) -> Value {
    let result = text(check, "result").unwrap_or("");
    let qualifiers = check
        .get("qualifiers")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if result == "exceeded" {
        let requested = check
            .get("requested_days")
            .or_else(|| check.get("daily_amount"));
        let maximum = check
            .get("maximum_days")
            .or_else(|| check.get("maximum_daily_amount"));
        let unit = text(check, "unit").unwrap_or(if category == "duration_caution" {
            "일"
        } else {
            ""
        });
        let details = requested.zip(maximum).map(|(requested, maximum)| {
            if category == "dose_caution" {
                format!(
                    "입력한 1일 용량 {}{} · DUR 기준 {}{}",
                    scalar_text(requested),
                    unit,
                    scalar_text(maximum),
                    unit
                )
            } else {
                format!(
                    "입력한 투여기간 {}{} · DUR 기준 {}{}",
                    scalar_text(requested),
                    unit,
                    scalar_text(maximum),
                    unit
                )
            }
        });
        return item_owned(
            category,
            label,
            "hit",
            format!("{label} 기준 초과"),
            details,
            Vec::new(),
            qualifiers,
        );
    }
    if !dataset_verified {
        return item(
            category,
            label,
            "unknown",
            "자동 확인 제한",
            Some("필수 DUR 원본을 검증하지 못해 확인할 수 없습니다."),
            Vec::new(),
            qualifiers,
        );
    }
    if result == "not_evaluable" {
        let conditional = text(check, "evaluation_status") == Some("conditional");
        return item_owned(
            category,
            label,
            if conditional {
                "conditional"
            } else {
                "unknown"
            },
            if conditional {
                format!("{label} 조건 확인 필요")
            } else {
                "확인 필요".to_owned()
            },
            Some(friendly_quantitative_reason(category, check)),
            Vec::new(),
            qualifiers,
        );
    }
    if !mapping_complete {
        return item(
            category,
            label,
            "unknown",
            "자동 확인 제한",
            Some(mapping_reason),
            Vec::new(),
            qualifiers,
        );
    }
    if result == "within" {
        let requested = check
            .get("requested_days")
            .or_else(|| check.get("daily_amount"));
        let maximum = check
            .get("maximum_days")
            .or_else(|| check.get("maximum_daily_amount"));
        let unit = text(check, "unit").unwrap_or(if category == "duration_caution" {
            "일"
        } else {
            ""
        });
        let details = requested.zip(maximum).map(|(requested, maximum)| {
            format!(
                "입력값 {}{} · 기준 {}{}",
                scalar_text(requested),
                unit,
                scalar_text(maximum),
                unit
            )
        });
        return item_owned(
            category,
            label,
            "clear",
            "기준 이내".to_owned(),
            details,
            Vec::new(),
            qualifiers,
        );
    }
    if result == "not_applicable" {
        return item(
            category,
            label,
            "not_applicable",
            "해당 기준 없음",
            None,
            Vec::new(),
            qualifiers,
        );
    }
    item(
        category,
        label,
        "unknown",
        "확인 필요",
        Some("자동 판정 결과를 확정하지 못했습니다."),
        Vec::new(),
        qualifiers,
    )
}

fn friendly_quantitative_reason(category: &str, check: &Map<String, Value>) -> String {
    let reason = text(check, "reason").unwrap_or("");
    if check
        .get("pediatric_review")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return "소아 용량은 나이·체중·적응증에 따른 별도 처방 기준 확인이 필요합니다.".to_owned();
    }
    if category == "duration_caution" && reason == "prescription duration is missing or invalid" {
        return "처방 일수를 입력하면 투여기간 기준을 확인할 수 있습니다.".to_owned();
    }
    if category == "dose_caution"
        && (reason.contains("dose input is missing")
            || reason.contains("daily frequency is missing"))
    {
        return "1회 복용량과 1일 횟수를 입력하면 용량 기준을 확인할 수 있습니다.".to_owned();
    }
    if reason.contains("dosage form") {
        return "제품 제형을 확정하지 못해 자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요."
            .to_owned();
    }
    if !reason.is_empty() {
        return "DUR 기준을 하나의 조건으로 확정하지 못해 자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요."
            .to_owned();
    }
    "자동 판정하지 못했습니다. 의사 또는 약사에게 확인하세요.".to_owned()
}

pub(crate) fn mapping_complete(category: &str, coverage: &Map<String, Value>) -> bool {
    let product_matched = coverage
        .get("product")
        .and_then(Value::as_object)
        .and_then(|product| text(product, "status"))
        == Some("matched");
    let unresolved = coverage
        .get("category_resolution")
        .and_then(Value::as_object)
        .and_then(|issues| issues.get(category))
        .and_then(Value::as_str)
        == Some("unresolved");
    product_matched && !unresolved
}

pub(crate) fn mapping_reason(category: &str, coverage: &Map<String, Value>) -> String {
    let unresolved = coverage
        .get("category_resolution")
        .and_then(Value::as_object)
        .and_then(|issues| issues.get(category))
        .and_then(Value::as_str)
        == Some("unresolved");
    if unresolved {
        return "DUR 상세 기준을 자동으로 하나로 확정하지 못했습니다. 의사 또는 약사에게 확인하세요."
            .to_owned();
    }
    let product_matched = coverage
        .get("product")
        .and_then(Value::as_object)
        .and_then(|product| text(product, "status"))
        == Some("matched");
    if !product_matched {
        return "MFDS ITEM_SEQ 제품을 canonical 데이터에 연결하지 못했습니다.".to_owned();
    }
    "DUR 판정 범위를 완전히 확인하지 못했습니다. 의사 또는 약사에게 확인하세요.".to_owned()
}

pub(crate) fn category_issue(category: &str, coverage: &Map<String, Value>) -> Option<String> {
    coverage
        .get("not_evaluable_checks")
        .and_then(Value::as_array)?
        .iter()
        .find(|issue| issue.get("category").and_then(Value::as_str) == Some(category))
        .map(|issue| {
            issue
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("자동 판정하지 못했습니다.")
                .to_owned()
        })
}
