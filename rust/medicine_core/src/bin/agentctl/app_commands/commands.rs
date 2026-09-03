use super::support::{
    encode_path, encode_query, insert_i64_required, insert_optional_f64, insert_optional_i64,
    insert_optional_string, parse, query_string, AppConfig,
};
use medicine_core::{initialize_personal_db, MedicineEngine};
use serde_json::{json, Map, Value};

pub(super) fn dispatch(
    config: &AppConfig,
    command: &str,
    args: &[String],
    usage: fn() -> String,
) -> Result<Value, String> {
    initialize_personal_db(&config.personal_db)?;
    let (method, path, body) = match command {
        "people" => {
            parse(args, &[], &["--json"], usage)?.expect_no_positionals()?;
            ("GET", "/api/people".to_owned(), None)
        }
        "person-add" => person_add(args, usage)?,
        "person-update" => person_update(args, usage)?,
        "person-delete" => person_delete(args, usage)?,
        "drug-search" => drug_search(args, usage)?,
        "meds" => medications(args, usage)?,
        "risk-preview" => medication_write(args, usage, MedicationWriteKind::Preview)?,
        "med-add" => medication_write(args, usage, MedicationWriteKind::Create)?,
        "med-update" => medication_update(args, usage)?,
        "med-history" => medication_history(args, usage)?,
        "med-stop" => medication_stop(args, usage)?,
        "daily-plan" => daily_plan(args, usage)?,
        "reminders" => reminders(args, usage)?,
        "reminder-resolve" => reminder_resolve(args, usage)?,
        "dose-instance" => dose_instance(args, usage)?,
        "dose-instance-cancel" => dose_instance_cancel(args, usage)?,
        "prn-intake" => prn_intake(args, usage)?,
        _ => return Err(usage()),
    };
    request(config, method, &path, body)
}

fn person_add(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &[
            "--name",
            "--birth-date",
            "--sex",
            "--pregnancy-status",
            "--lactation-status",
        ],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let body = json!({
        "name": parsed.required("--name")?,
        "birth_date": parsed.required("--birth-date")?,
        "sex": parsed.required("--sex")?,
        "pregnancy_status": parsed.optional("--pregnancy-status").unwrap_or("unknown"),
        "lactation_status": parsed.optional("--lactation-status").unwrap_or("unknown"),
    });
    Ok(("POST", "/api/people".to_owned(), Some(body)))
}

fn person_update(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &[
            "--person",
            "--name",
            "--birth-date",
            "--sex",
            "--pregnancy-status",
            "--lactation-status",
        ],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let person = encode_path(parsed.required("--person")?);
    let body = json!({
        "name": parsed.required("--name")?,
        "birth_date": parsed.required("--birth-date")?,
        "sex": parsed.required("--sex")?,
        "pregnancy_status": parsed.required("--pregnancy-status")?,
        "lactation_status": parsed.required("--lactation-status")?,
    });
    Ok(("PATCH", format!("/api/people/{person}"), Some(body)))
}

fn person_delete(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--person"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let person = encode_path(parsed.required("--person")?);
    Ok(("DELETE", format!("/api/people/{person}"), None))
}

fn drug_search(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &["--limit", "--offset"],
        &["--include-inactive", "--explain-matches", "--json"],
        usage,
    )?;
    if parsed.positionals.len() != 1 {
        return Err(usage());
    }
    let limit = parsed.parse_i64("--limit", 20)?;
    let offset = parsed.parse_i64("--offset", 0)?;
    let query = query_string(&[
        ("q", parsed.positionals[0].as_str().to_owned()),
        ("limit", limit.to_string()),
        ("offset", offset.to_string()),
        (
            "include_inactive",
            if parsed.flag("--include-inactive") {
                "true"
            } else {
                "false"
            }
            .to_owned(),
        ),
        (
            "explain_matches",
            if parsed.flag("--explain-matches") {
                "true"
            } else {
                "false"
            }
            .to_owned(),
        ),
    ]);
    Ok(("GET", format!("/api/products?{query}"), None))
}

fn medications(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--person", "--date"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let person = encode_path(parsed.required("--person")?);
    let query = parsed
        .optional("--date")
        .map(|date| format!("?date={}", encode_query(date)))
        .unwrap_or_default();
    Ok((
        "GET",
        format!("/api/people/{person}/medications{query}"),
        None,
    ))
}

#[derive(Clone, Copy)]
enum MedicationWriteKind {
    Preview,
    Create,
}

fn medication_write(
    args: &[String],
    usage: fn() -> String,
    kind: MedicationWriteKind,
) -> Result<(&'static str, String, Option<Value>), String> {
    let mut value_options = vec![
        "--person",
        "--product-ref",
        "--product-code",
        "--dose-amount",
        "--dose-unit",
        "--frequency",
        "--meal-relation",
        "--route",
        "--prn-max",
        "--days",
        "--start-date",
        "--end-date",
        "--time",
    ];
    if matches!(kind, MedicationWriteKind::Create) {
        value_options.extend(["--dose", "--request-id", "--warning-token"]);
    }
    let switches = if matches!(kind, MedicationWriteKind::Create) {
        vec!["--prn", "--long-term", "--acknowledge-warnings", "--json"]
    } else {
        vec!["--prn", "--long-term", "--json"]
    };
    let parsed = parse(args, &value_options, &switches, usage)?;
    parsed.expect_no_positionals()?;
    let person = encode_path(parsed.required("--person")?);
    let product_ref = parsed
        .one_of(&["--product-ref", "--product-code"])
        .ok_or_else(usage)?;
    let mut body = Map::new();
    body.insert(
        "product_ref".to_owned(),
        Value::String(product_ref.to_owned()),
    );
    if matches!(kind, MedicationWriteKind::Create) {
        insert_optional_string(&mut body, "dosage_text", parsed.optional("--dose"));
    }
    insert_optional_f64(&mut body, "dose_amount", parsed.optional("--dose-amount"))?;
    insert_optional_string(&mut body, "dose_unit", parsed.optional("--dose-unit"));
    insert_optional_i64(
        &mut body,
        "frequency_per_day",
        parsed.optional("--frequency"),
    )?;
    body.insert(
        "meal_relation".to_owned(),
        Value::String(
            parsed
                .optional("--meal-relation")
                .unwrap_or("unspecified")
                .to_owned(),
        ),
    );
    body.insert(
        "administration_route".to_owned(),
        Value::String(parsed.optional("--route").unwrap_or("unknown").to_owned()),
    );
    body.insert("as_needed".to_owned(), Value::Bool(parsed.flag("--prn")));
    insert_optional_i64(&mut body, "prn_max_per_day", parsed.optional("--prn-max"))?;
    insert_optional_i64(&mut body, "prescription_days", parsed.optional("--days"))?;
    body.insert(
        "long_term".to_owned(),
        Value::Bool(parsed.flag("--long-term")),
    );
    body.insert(
        "schedule_times".to_owned(),
        Value::Array(
            parsed
                .all("--time")
                .iter()
                .map(|value| Value::String((*value).to_owned()))
                .collect(),
        ),
    );
    insert_optional_string(&mut body, "start_date", parsed.optional("--start-date"));
    insert_optional_string(&mut body, "end_date", parsed.optional("--end-date"));
    if matches!(kind, MedicationWriteKind::Create) {
        insert_optional_string(&mut body, "request_id", parsed.optional("--request-id"));
        body.insert(
            "acknowledge_warnings".to_owned(),
            Value::Bool(parsed.flag("--acknowledge-warnings")),
        );
        insert_optional_string(
            &mut body,
            "warning_token",
            parsed.optional("--warning-token"),
        );
    }
    let suffix = if matches!(kind, MedicationWriteKind::Preview) {
        "/preview"
    } else {
        ""
    };
    Ok((
        "POST",
        format!("/api/people/{person}/medications{suffix}"),
        Some(Value::Object(body)),
    ))
}

fn medication_update(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &[
            "--medication",
            "--expected-revision",
            "--dose",
            "--dose-amount",
            "--dose-unit",
            "--frequency",
            "--meal-relation",
            "--route",
            "--prn-max",
            "--days",
            "--start-date",
            "--end-date",
            "--time",
            "--warning-token",
        ],
        &[
            "--prn",
            "--scheduled",
            "--long-term",
            "--bounded",
            "--acknowledge-warnings",
            "--json",
        ],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    if parsed.flag("--prn") && parsed.flag("--scheduled") {
        return Err(usage());
    }
    if parsed.flag("--long-term") && parsed.flag("--bounded") {
        return Err(usage());
    }
    let medication = encode_path(parsed.required("--medication")?);
    let mut body = Map::new();
    insert_i64_required(
        &mut body,
        "expected_revision",
        parsed.required("--expected-revision")?,
    )?;
    insert_optional_string(&mut body, "dosage_text", parsed.optional("--dose"));
    insert_optional_f64(&mut body, "dose_amount", parsed.optional("--dose-amount"))?;
    insert_optional_string(&mut body, "dose_unit", parsed.optional("--dose-unit"));
    insert_optional_i64(
        &mut body,
        "frequency_per_day",
        parsed.optional("--frequency"),
    )?;
    insert_optional_string(
        &mut body,
        "meal_relation",
        parsed.optional("--meal-relation"),
    );
    insert_optional_string(
        &mut body,
        "administration_route",
        parsed.optional("--route"),
    );
    if parsed.flag("--prn") || parsed.flag("--scheduled") {
        body.insert("as_needed".to_owned(), Value::Bool(parsed.flag("--prn")));
    }
    insert_optional_i64(&mut body, "prn_max_per_day", parsed.optional("--prn-max"))?;
    insert_optional_i64(&mut body, "prescription_days", parsed.optional("--days"))?;
    if parsed.flag("--long-term") || parsed.flag("--bounded") {
        body.insert(
            "long_term".to_owned(),
            Value::Bool(parsed.flag("--long-term")),
        );
    }
    if parsed.values.contains_key("--time") {
        body.insert(
            "schedule_times".to_owned(),
            Value::Array(
                parsed
                    .all("--time")
                    .iter()
                    .map(|value| Value::String((*value).to_owned()))
                    .collect(),
            ),
        );
    }
    insert_optional_string(&mut body, "start_date", parsed.optional("--start-date"));
    insert_optional_string(&mut body, "end_date", parsed.optional("--end-date"));
    body.insert(
        "acknowledge_warnings".to_owned(),
        Value::Bool(parsed.flag("--acknowledge-warnings")),
    );
    insert_optional_string(
        &mut body,
        "warning_token",
        parsed.optional("--warning-token"),
    );
    Ok((
        "PATCH",
        format!("/api/medications/{medication}"),
        Some(Value::Object(body)),
    ))
}

fn medication_history(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--medication"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let medication = encode_path(parsed.required("--medication")?);
    Ok((
        "GET",
        format!("/api/medications/{medication}/history"),
        None,
    ))
}

fn medication_stop(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &["--medication", "--expected-revision"],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let medication = encode_path(parsed.required("--medication")?);
    let revision = parsed.parse_required_i64("--expected-revision")?;
    Ok((
        "DELETE",
        format!("/api/medications/{medication}?expected_revision={revision}"),
        None,
    ))
}

fn daily_plan(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--person", "--date"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let person = encode_path(parsed.required("--person")?);
    let query = parsed
        .optional("--date")
        .map(|date| format!("?date={}", encode_query(date)))
        .unwrap_or_default();
    Ok((
        "GET",
        format!("/api/people/{person}/daily-plan{query}"),
        None,
    ))
}

fn reminders(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--from", "--days"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let from = encode_query(parsed.required("--from")?);
    let days = parsed.parse_required_i64("--days")?;
    Ok((
        "GET",
        format!("/api/reminders/upcoming?from={from}&days={days}"),
        None,
    ))
}

fn reminder_resolve(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &[
            "--person",
            "--medication",
            "--date",
            "--schedule-key",
            "--scheduled-at",
        ],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    Ok((
        "POST",
        "/api/reminders/resolve".to_owned(),
        Some(json!({
            "person_id": parsed.required("--person")?,
            "medication_id": parsed.required("--medication")?,
            "scheduled_date": parsed.required("--date")?,
            "schedule_key": parsed.required("--schedule-key")?,
            "scheduled_at": parsed.required("--scheduled-at")?,
        })),
    ))
}

fn dose_instance(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &["--instance", "--status", "--at"],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let instance = encode_path(parsed.required("--instance")?);
    let status = parsed.required("--status")?;
    if !matches!(status, "taken" | "skipped") {
        return Err(usage());
    }
    let mut body = Map::from_iter([("status".to_owned(), Value::String(status.to_owned()))]);
    insert_optional_string(&mut body, "occurred_at", parsed.optional("--at"));
    Ok((
        "POST",
        format!("/api/dose-instances/{instance}"),
        Some(Value::Object(body)),
    ))
}

fn dose_instance_cancel(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(args, &["--instance"], &["--json"], usage)?;
    parsed.expect_no_positionals()?;
    let instance = encode_path(parsed.required("--instance")?);
    Ok((
        "DELETE",
        format!("/api/dose-instances/{instance}/completion"),
        None,
    ))
}

fn prn_intake(
    args: &[String],
    usage: fn() -> String,
) -> Result<(&'static str, String, Option<Value>), String> {
    let parsed = parse(
        args,
        &["--medication", "--at", "--note", "--request-id"],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let medication = encode_path(parsed.required("--medication")?);
    let mut body = Map::new();
    body.insert(
        "request_id".to_owned(),
        Value::String(parsed.required("--request-id")?.to_owned()),
    );
    insert_optional_string(&mut body, "occurred_at", parsed.optional("--at"));
    insert_optional_string(&mut body, "note", parsed.optional("--note"));
    Ok((
        "POST",
        format!("/api/medications/{medication}/prn-intakes"),
        Some(Value::Object(body)),
    ))
}

fn request(
    config: &AppConfig,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    let engine = MedicineEngine::new(
        Some(config.canonical_db.as_path()),
        Some(config.personal_db.as_path()),
        None,
    );
    let body = body.map(|value| value.to_string()).unwrap_or_default();
    let (response, observation) = engine.request_with_observation(method, path, &body);
    super::super::runtime_log::record_or_emit(&observation);
    serde_json::from_str(&response)
        .map_err(|error| format!("medicine engine returned invalid JSON: {error}"))
}
