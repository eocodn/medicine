use super::ui_runtime::{UiRuntime, UiRuntimeConfig};
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

const MAX_OPERATIONS: usize = 64;
const MAX_HORIZON_MS: u64 = 60_000;
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(5);
const DEFAULT_STATIC_DIR: &str = "ui/dist";

struct Operation {
    index: usize,
    id: String,
    at_ms: u64,
    action: String,
    value: Value,
}

struct ScenarioClock {
    origin: Instant,
    last_heartbeat: Instant,
    completed: usize,
    total: usize,
}

pub(super) fn run(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let parsed = parse_args(args, usage)?;
    let operations = parse_operations(&parsed.input)?;
    let mut runtime = UiRuntime::start(
        &UiRuntimeConfig {
            canonical_db: parsed.canonical_db,
            personal_db: parsed.personal_db,
        },
        &parsed.static_dir,
        parsed.reference_unavailable_reason.as_deref(),
        parsed.width,
        parsed.height,
    )?;
    emit_event(json!({"event":"ui_scenario_started","operation_count":operations.len()}));
    let mut clock = ScenarioClock::new(operations.len());
    let mut results = vec![Value::Null; operations.len()];
    for operation in operations {
        clock.wait_until(operation.at_ms);
        emit_event(json!({
            "event":"ui_operation_started",
            "index":operation.index,
            "id":operation.id,
            "action":operation.action,
            "at_ms":operation.at_ms
        }));
        let result = execute_operation(&mut runtime, &operation, &mut clock)?;
        for event in runtime.take_browser_events() {
            emit_event(json!({"event":"browser_event","detail":event}));
        }
        emit_event(json!({
            "event":"ui_operation_completed",
            "index":operation.index,
            "id":operation.id,
            "action":operation.action
        }));
        results[operation.index] = json!({
            "id": operation.id,
            "action": operation.action,
            "result": result
        });
        clock.completed += 1;
    }
    let payload = json!({
        "status":"completed",
        "operation_count":results.len(),
        "elapsed_ms":clock.origin.elapsed().as_millis(),
        "results":results
    });
    if parsed.json_output {
        println!("{payload}");
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(&payload)
                .map_err(|error| format!("cannot encode UI scenario result: {error}"))?
        );
    }
    Ok(())
}

fn execute_operation(
    runtime: &mut UiRuntime,
    operation: &Operation,
    clock: &mut ScenarioClock,
) -> Result<Value, String> {
    let object = operation
        .value
        .as_object()
        .ok_or_else(|| format!("UI operation {} must be an object", operation.id))?;
    match operation.action.as_str() {
        "open" => {
            runtime.open(optional_string(object, "screen")?)?;
            Ok(json!({"opened":true}))
        }
        "reload" => {
            runtime.reload()?;
            Ok(json!({"reloaded":true}))
        }
        "click" => runtime.click(required_string(object, "target")?),
        "set-field" => {
            let target = required_string(object, "target")?;
            runtime.set_field(target, object.get("value").unwrap_or(&Value::Null))
        }
        "observe" => runtime.observe(),
        "set-storage" => {
            let key = required_string(object, "key")?;
            let value = optional_string(object, "value")?;
            runtime.set_local_storage(key, value)?;
            Ok(json!({"updated":true,"key":key}))
        }
        "request" => {
            let method = required_string(object, "method")?;
            let path = required_string(object, "path")?;
            if !path.starts_with('/') {
                return Err(format!(
                    "UI operation {} request path must be absolute",
                    operation.id
                ));
            }
            runtime.request(method, path, object.get("body"))
        }
        "fault" => {
            let mut rule = Map::new();
            for key in [
                "fault_id", "method", "path", "times", "status", "body", "delay_ms",
            ] {
                if let Some(value) = object.get(key) {
                    let target = if key == "fault_id" { "id" } else { key };
                    rule.insert(target.to_owned(), value.clone());
                }
            }
            let fault_action = required_string(object, "fault_action")?;
            rule.insert("action".to_owned(), Value::String(fault_action.to_owned()));
            runtime.install_fault(&Value::Object(rule))
        }
        "wait-request" => {
            let id = required_string(object, "fault_id")?;
            let timeout_ms = optional_u64(object, "timeout_ms")?.unwrap_or(5_000);
            let deadline = Instant::now() + Duration::from_millis(timeout_ms);
            loop {
                let status = runtime.fault_status(id)?;
                if status.get("waiting").and_then(Value::as_u64).unwrap_or(0) > 0 {
                    return Ok(status);
                }
                if Instant::now() >= deadline {
                    return Err(format!("fault gate did not reach waiting state: {id}"));
                }
                clock.heartbeat_if_due();
                thread::sleep(Duration::from_millis(20));
            }
        }
        "release" => runtime.release_fault(required_string(object, "fault_id")?),
        "wait-state" => {
            let field = required_string(object, "field")?;
            let expected = object.get("equals").ok_or_else(|| {
                format!("UI operation {} wait-state requires equals", operation.id)
            })?;
            let timeout_ms = optional_u64(object, "timeout_ms")?.unwrap_or(5_000);
            let deadline = Instant::now() + Duration::from_millis(timeout_ms);
            loop {
                let state = runtime.observe()?;
                if lookup_field(&state, field) == Some(expected) {
                    return Ok(state);
                }
                if Instant::now() >= deadline {
                    return Err(format!(
                        "UI state did not converge: field={field}, expected={expected}, actual={}",
                        lookup_field(&state, field).unwrap_or(&Value::Null)
                    ));
                }
                clock.heartbeat_if_due();
                thread::sleep(Duration::from_millis(20));
            }
        }
        "clock-set" => runtime.set_clock(required_string(object, "datetime")?),
        "screenshot" => {
            let output = PathBuf::from(required_string(object, "output")?);
            runtime.screenshot(&output)
        }
        action => Err(format!("unsupported UI scenario action: {action}")),
    }
}

fn lookup_field<'a>(value: &'a Value, field: &str) -> Option<&'a Value> {
    let mut current = value;
    for part in field.split('.') {
        current = current.get(part)?;
    }
    Some(current)
}

struct ParsedArgs {
    canonical_db: PathBuf,
    personal_db: PathBuf,
    static_dir: PathBuf,
    reference_unavailable_reason: Option<String>,
    input: String,
    width: u32,
    height: u32,
    json_output: bool,
}

fn parse_args(args: &[String], usage: fn() -> String) -> Result<ParsedArgs, String> {
    let mut canonical_db = PathBuf::from("data/db/mobile.sqlite");
    let mut personal_db: Option<PathBuf> = None;
    let mut static_dir = std::env::var_os("MEDICINE_STATIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_STATIC_DIR));
    let mut reference_unavailable_reason = None;
    let mut input = None;
    let mut input_file = None;
    let mut width = 390_u32;
    let mut height = 844_u32;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = PathBuf::from(args.get(index).ok_or_else(usage)?);
            }
            "--personal-db" => {
                index += 1;
                personal_db = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--static-dir" => {
                index += 1;
                static_dir = PathBuf::from(args.get(index).ok_or_else(usage)?);
            }
            "--reference-unavailable-reason" => {
                index += 1;
                reference_unavailable_reason = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--input" => {
                index += 1;
                input = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--input-file" => {
                index += 1;
                input_file = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--width" => {
                index += 1;
                width = args
                    .get(index)
                    .ok_or_else(usage)?
                    .parse()
                    .map_err(|_| "ui-scenario width must be a positive integer".to_owned())?;
            }
            "--height" => {
                index += 1;
                height = args
                    .get(index)
                    .ok_or_else(usage)?
                    .parse()
                    .map_err(|_| "ui-scenario height must be a positive integer".to_owned())?;
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    if width == 0 || height == 0 {
        return Err("ui-scenario width and height must be positive".to_owned());
    }
    if input.is_some() && input_file.is_some() {
        return Err("ui-scenario accepts only one of --input or --input-file".to_owned());
    }
    let input = match (input, input_file) {
        (Some(value), None) => value,
        (None, Some(path)) => std::fs::read_to_string(&path)
            .map_err(|error| format!("cannot read UI scenario {}: {error}", path.display()))?,
        _ => return Err("ui-scenario requires --input or --input-file".to_owned()),
    };
    let personal_db = personal_db.ok_or_else(|| "ui-scenario requires --personal-db".to_owned())?;
    if !static_dir.join("index.html").is_file() {
        return Err(format!(
            "UI static index is unavailable: {}",
            static_dir.join("index.html").display()
        ));
    }
    Ok(ParsedArgs {
        canonical_db,
        personal_db,
        static_dir,
        reference_unavailable_reason,
        input,
        width,
        height,
        json_output,
    })
}

fn parse_operations(input: &str) -> Result<Vec<Operation>, String> {
    let value: Value = serde_json::from_str(input)
        .map_err(|error| format!("UI scenario input must be valid JSON: {error}"))?;
    let rows = value
        .get("operations")
        .and_then(Value::as_array)
        .ok_or_else(|| "UI scenario input requires an operations array".to_owned())?;
    if rows.is_empty() || rows.len() > MAX_OPERATIONS {
        return Err(format!(
            "UI scenario operations must contain between 1 and {MAX_OPERATIONS} entries"
        ));
    }
    let mut ids = HashSet::new();
    let mut operations = Vec::with_capacity(rows.len());
    for (index, row) in rows.iter().enumerate() {
        let object = row
            .as_object()
            .ok_or_else(|| format!("UI scenario operation {index} must be an object"))?;
        let id = required_string(object, "id")?.to_owned();
        if !ids.insert(id.clone()) {
            return Err(format!("UI scenario operation id is duplicated: {id}"));
        }
        let at_ms = object
            .get("at_ms")
            .and_then(Value::as_u64)
            .ok_or_else(|| format!("UI scenario operation {id} requires non-negative at_ms"))?;
        if at_ms > MAX_HORIZON_MS {
            return Err(format!(
                "UI scenario operation {id} exceeds {MAX_HORIZON_MS}ms horizon"
            ));
        }
        let action = required_string(object, "action")?.to_owned();
        operations.push(Operation {
            index,
            id,
            at_ms,
            action,
            value: row.clone(),
        });
    }
    operations.sort_by_key(|operation| (operation.at_ms, operation.index));
    Ok(operations)
}

impl ScenarioClock {
    fn new(total: usize) -> Self {
        let origin = Instant::now();
        Self {
            origin,
            last_heartbeat: origin,
            completed: 0,
            total,
        }
    }

    fn wait_until(&mut self, at_ms: u64) {
        let target = self.origin + Duration::from_millis(at_ms);
        loop {
            let now = Instant::now();
            if now >= target {
                return;
            }
            self.heartbeat_if_due();
            thread::sleep((target - now).min(Duration::from_millis(50)));
        }
    }

    fn heartbeat_if_due(&mut self) {
        if self.last_heartbeat.elapsed() < HEARTBEAT_INTERVAL {
            return;
        }
        self.last_heartbeat = Instant::now();
        emit_event(json!({
            "event":"heartbeat",
            "completed":self.completed,
            "total":self.total,
            "elapsed_ms":self.origin.elapsed().as_millis()
        }));
    }
}

fn required_string<'a>(object: &'a Map<String, Value>, key: &str) -> Result<&'a str, String> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("{key} is required"))
}

fn optional_string<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a str>, String> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value)),
        _ => Err(format!("{key} must be a string")),
    }
}

fn optional_u64(object: &Map<String, Value>, key: &str) -> Result<Option<u64>, String> {
    match object.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(value)) => value
            .as_u64()
            .map(Some)
            .ok_or_else(|| format!("{key} must be non-negative")),
        _ => Err(format!("{key} must be a non-negative integer")),
    }
}

fn emit_event(event: Value) {
    eprintln!("{event}");
}
