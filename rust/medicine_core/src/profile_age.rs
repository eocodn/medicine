use chrono::{Datelike, Duration, NaiveDate};
use regex::Regex;
use std::collections::BTreeSet;
use std::sync::OnceLock;

use crate::safety_time::today_kst;

pub(crate) fn evaluate_age_rule(
    birth_date: &str,
    rule: Option<&str>,
    product_dosage_form: Option<&str>,
    as_of: Option<NaiveDate>,
) -> (Option<bool>, Option<String>) {
    let as_of = as_of.unwrap_or_else(today_kst);
    let Some(rule) = rule.filter(|value| !value.is_empty()) else {
        return (None, Some("연령금기 기준값이 없습니다.".to_owned()));
    };
    let matches = age_rule_regex().captures_iter(rule).collect::<Vec<_>>();
    if matches.is_empty() {
        return (
            None,
            Some("연령금기 기준을 자동으로 해석하지 못했습니다.".to_owned()),
        );
    }
    if matches.len() == 1 {
        return match age_match_applies(birth_date, &matches[0], as_of) {
            Some(value) => (Some(value), None),
            None => (
                None,
                Some("연령금기 기준을 자동으로 해석하지 못했습니다.".to_owned()),
            ),
        };
    }

    let product_tags = dosage_form_tags(product_dosage_form.unwrap_or(""));
    if product_tags.is_empty() {
        return (
            None,
            Some(
                "제품 제형을 확정하지 못해 제형별 연령금기 기준을 자동 판정하지 못했습니다."
                    .to_owned(),
            ),
        );
    }

    let mut groups = Vec::with_capacity(matches.len());
    let mut previous_end = 0;
    for captures in &matches {
        let whole = captures.get(0).expect("age regex always has a whole match");
        let prefix = &rule[previous_end..whole.start()];
        previous_end = whole.end();
        let Some((form_text, _)) = prefix.rsplit_once(':') else {
            return (
                None,
                Some("제형별 연령금기 조건 구조를 하나로 확정하지 못했습니다.".to_owned()),
            );
        };
        let tags = dosage_form_tags(form_text);
        if tags.is_empty() {
            return (
                None,
                Some("제형별 연령금기 조건의 제형을 자동으로 해석하지 못했습니다.".to_owned()),
            );
        }
        groups.push((tags, captures));
    }

    let applicable = groups
        .into_iter()
        .filter(|(tags, _)| tags.iter().any(|tag| product_tags.contains(tag)))
        .map(|(_, captures)| captures)
        .collect::<Vec<_>>();
    if applicable.len() != 1 {
        return (
            None,
            Some("제품 제형에 적용되는 연령금기 조건을 하나로 확정하지 못했습니다.".to_owned()),
        );
    }
    match age_match_applies(birth_date, applicable[0], as_of) {
        Some(value) => (Some(value), None),
        None => (
            None,
            Some("연령금기 기준을 자동으로 해석하지 못했습니다.".to_owned()),
        ),
    }
}

fn age_match_applies(
    birth_date: &str,
    captures: &regex::Captures<'_>,
    as_of: NaiveDate,
) -> Option<bool> {
    let birth = NaiveDate::parse_from_str(birth_date, "%Y-%m-%d").ok()?;
    let amount = captures.name("n")?.as_str().parse::<i32>().ok()?;
    let unit = captures.name("unit")?.as_str();
    let operator = captures.name("op")?.as_str();
    let threshold = threshold_date(birth, amount, unit)?;
    let next_threshold = threshold_date(birth, amount.checked_add(1)?, unit)?;
    match operator {
        "미만" => Some(as_of < threshold),
        "이하" => Some(as_of < next_threshold),
        "이상" => Some(as_of >= threshold),
        "초과" => Some(as_of >= next_threshold),
        _ => None,
    }
}

fn threshold_date(birth: NaiveDate, amount: i32, unit: &str) -> Option<NaiveDate> {
    match unit {
        "세" => add_years(birth, amount),
        "개월" => add_months(birth, amount),
        "주" => birth.checked_add_signed(Duration::weeks(i64::from(amount))),
        "일" => birth.checked_add_signed(Duration::days(i64::from(amount))),
        _ => None,
    }
}

fn add_years(value: NaiveDate, years: i32) -> Option<NaiveDate> {
    let year = value.year().checked_add(years)?;
    value
        .with_year(year)
        .or_else(|| NaiveDate::from_ymd_opt(year, 2, 28))
}

fn add_months(value: NaiveDate, months: i32) -> Option<NaiveDate> {
    let month_index = value
        .year()
        .checked_mul(12)?
        .checked_add(i32::try_from(value.month0()).ok()?)?
        .checked_add(months)?;
    let year = month_index.div_euclid(12);
    let month0 = month_index.rem_euclid(12);
    let month = u32::try_from(month0).ok()?.checked_add(1)?;
    let day = value.day().min(days_in_month(year, month)?);
    NaiveDate::from_ymd_opt(year, month, day)
}

fn days_in_month(year: i32, month: u32) -> Option<u32> {
    let (next_year, next_month) = if month == 12 {
        (year.checked_add(1)?, 1)
    } else {
        (year, month.checked_add(1)?)
    };
    let first_next = NaiveDate::from_ymd_opt(next_year, next_month, 1)?;
    first_next
        .checked_sub_signed(Duration::days(1))
        .map(|value| value.day())
}

fn dosage_form_tags(value: &str) -> BTreeSet<&'static str> {
    let text = value.trim();
    let mut tags = BTreeSet::new();
    if text.is_empty() {
        return tags;
    }
    add_tag_if(&mut tags, text.contains("점안"), "점안제");
    add_tag_if(&mut tags, text.contains("점이"), "점이제");
    add_tag_if(&mut tags, text.contains("점비"), "점비제");
    add_tag_if(
        &mut tags,
        text.contains("주사") || text.contains("수액"),
        "주사제",
    );
    add_tag_if(&mut tags, text.contains("흡입"), "흡입제");
    add_tag_if(&mut tags, text.contains("크림"), "크림제");
    add_tag_if(&mut tags, text.contains("연고"), "연고제");
    add_tag_if(&mut tags, text.contains("로션"), "로션제");
    add_tag_if(&mut tags, text.contains("겔"), "겔제");
    add_tag_if(
        &mut tags,
        text.contains("피부액") || text.contains("외용액"),
        "외용액제",
    );
    add_tag_if(&mut tags, text.contains("좌제"), "좌제");
    add_tag_if(&mut tags, text.contains("경피흡수"), "경피흡수제");
    add_tag_if(
        &mut tags,
        text.contains("첩부") || text.contains("카타플라스마"),
        "첩부제",
    );
    add_tag_if(&mut tags, text.contains("구강정"), "구강정");
    add_tag_if(&mut tags, text.contains("박칼정"), "박칼정");
    add_tag_if(&mut tags, text.contains("설하정"), "설하정");
    add_tag_if(
        &mut tags,
        text.contains("구강붕해필름") || text.contains("구강용해필름"),
        "구강붕해필름",
    );
    add_tag_if(&mut tags, text.contains("캡슐"), "캡슐제");
    add_tag_if(&mut tags, is_tablet_form(text), "정제");
    add_tag_if(&mut tags, text.contains("시럽"), "시럽제");
    add_tag_if(&mut tags, text.contains("과립"), "과립제");
    add_tag_if(&mut tags, text.contains("세립"), "세립제");
    add_tag_if(
        &mut tags,
        text.contains("산제") && !contains_any(text, &["외용", "피부", "주사"]),
        "산제",
    );
    add_tag_if(
        &mut tags,
        text.contains("액제")
            && !contains_any(text, &["점안", "점이", "점비", "주사", "피부", "외용"]),
        "액제",
    );
    if text.contains("경구") {
        tags.insert(if text.contains('액') {
            "액제"
        } else {
            "경구제"
        });
    }
    tags
}

fn is_tablet_form(text: &str) -> bool {
    contains_any(
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
        ],
    ) || tablet_regex().is_match(text)
}

fn add_tag_if(tags: &mut BTreeSet<&'static str>, condition: bool, tag: &'static str) {
    if condition {
        tags.insert(tag);
    }
}

fn contains_any(text: &str, markers: &[&str]) -> bool {
    markers.iter().any(|marker| text.contains(marker))
}

fn age_rule_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?P<n>\d+)\s*(?P<unit>세|개월|주|일)\s*(?P<op>미만|이하|이상|초과)")
            .expect("valid age rule regex")
    })
}

fn tablet_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?:^|[,\s(])정제(?:$|[,\s)])").expect("valid tablet dosage form regex")
    })
}
