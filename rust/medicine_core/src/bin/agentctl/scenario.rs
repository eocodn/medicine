use medicine_core::{initialize_personal_db, MedicineEngine};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

const MAX_OPERATIONS: usize = 64;
const MAX_HORIZON_MS: u64 = 60_000;
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(5);

#[derive(Debug)]
struct Operation {
    index: usize,
    id: String,
    at_ms: u64,
    method: String,
    path: String,
    body: String,
}

enum Event {
    Started {
        index: usize,
        id: String,
        at_ms: u64,
    },
    Completed {
        index: usize,
        id: String,
        response: String,
    },
    Failed {
        index: usize,
        id: String,
        detail: String,
    },
}

pub fn run(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let mut canonical_db: Option<PathBuf> = None;
    let mut personal_db: Option<PathBuf> = None;
    let mut input: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--personal-db" => {
                index += 1;
                personal_db = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--input" => {
                index += 1;
                input = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }

    let personal_db = personal_db.ok_or_else(|| "scenario requires --personal-db".to_owned())?;
    initialize_personal_db(&personal_db)?;
    let operations =
        parse_operations(&input.ok_or_else(|| "scenario requires --input".to_owned())?)?;
    execute(canonical_db, personal_db, operations, json_output)
}

fn parse_operations(input: &str) -> Result<Vec<Operation>, String> {
    let value: Value = serde_json::from_str(input)
        .map_err(|error| format!("scenario input must be valid JSON: {error}"))?;
    let rows = value
        .get("operations")
        .and_then(Value::as_array)
        .ok_or_else(|| "scenario input requires an operations array".to_owned())?;
    if rows.is_empty() || rows.len() > MAX_OPERATIONS {
        return Err(format!(
            "scenario operations must contain between 1 and {MAX_OPERATIONS} entries"
        ));
    }

    let mut ids = HashSet::new();
    let mut operations = Vec::with_capacity(rows.len());
    for (index, value) in rows.iter().enumerate() {
        let object = value
            .as_object()
            .ok_or_else(|| format!("scenario operation {index} must be an object"))?;
        let id = object
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| format!("scenario operation {index} requires id"))?
            .to_owned();
        if !ids.insert(id.clone()) {
            return Err(format!("scenario operation id is duplicated: {id}"));
        }
        let at_ms = object
            .get("at_ms")
            .and_then(Value::as_u64)
            .ok_or_else(|| format!("scenario operation {id} requires non-negative at_ms"))?;
        if at_ms > MAX_HORIZON_MS {
            return Err(format!(
                "scenario operation {id} exceeds {MAX_HORIZON_MS}ms horizon"
            ));
        }
        let method = object
            .get("method")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| format!("scenario operation {id} requires method"))?
            .to_owned();
        let path = object
            .get("path")
            .and_then(Value::as_str)
            .filter(|value| value.starts_with('/'))
            .ok_or_else(|| format!("scenario operation {id} requires an absolute API path"))?
            .to_owned();
        let body = match object.get("body") {
            None | Some(Value::Null) => String::new(),
            Some(value) => serde_json::to_string(value)
                .map_err(|error| format!("cannot encode scenario operation {id} body: {error}"))?,
        };
        operations.push(Operation {
            index,
            id,
            at_ms,
            method,
            path,
            body,
        });
    }
    Ok(operations)
}

fn execute(
    canonical_db: Option<PathBuf>,
    personal_db: PathBuf,
    operations: Vec<Operation>,
    json_output: bool,
) -> Result<(), String> {
    let total = operations.len();
    let origin = Instant::now();
    let (sender, receiver) = mpsc::channel();
    let mut handles = Vec::with_capacity(total);

    for operation in operations {
        let sender = sender.clone();
        let canonical_db = canonical_db.clone();
        let personal_db = personal_db.clone();
        handles.push(thread::spawn(move || {
            sleep_until(origin, operation.at_ms);
            let _ = sender.send(Event::Started {
                index: operation.index,
                id: operation.id.clone(),
                at_ms: operation.at_ms,
            });
            let engine =
                MedicineEngine::new(canonical_db.as_deref(), Some(Path::new(&personal_db)), None);
            let response = engine.request(&operation.method, &operation.path, &operation.body);
            let event = if serde_json::from_str::<Value>(&response).is_ok() {
                Event::Completed {
                    index: operation.index,
                    id: operation.id,
                    response,
                }
            } else {
                Event::Failed {
                    index: operation.index,
                    id: operation.id,
                    detail: "medicine engine returned invalid JSON".to_owned(),
                }
            };
            let _ = sender.send(event);
        }));
    }
    drop(sender);

    let mut completed = 0usize;
    let mut results = vec![None; total];
    while completed < total {
        match receiver.recv_timeout(HEARTBEAT_INTERVAL) {
            Ok(Event::Started { index, id, at_ms }) => {
                emit_event(json!({
                    "event": "operation_started",
                    "index": index,
                    "id": id,
                    "at_ms": at_ms
                }));
            }
            Ok(Event::Completed {
                index,
                id,
                response,
            }) => {
                let envelope: Value = serde_json::from_str(&response)
                    .map_err(|error| format!("cannot decode scenario response: {error}"))?;
                emit_event(json!({
                    "event": "operation_completed",
                    "index": index,
                    "id": id,
                    "status": envelope.get("status")
                }));
                results[index] = Some(json!({"id": id, "response": envelope}));
                completed += 1;
            }
            Ok(Event::Failed { index, id, detail }) => {
                emit_event(json!({
                    "event": "operation_failed",
                    "index": index,
                    "id": id,
                    "detail": detail
                }));
                results[index] = Some(json!({"id": id, "error": detail}));
                completed += 1;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                emit_event(json!({
                    "event": "heartbeat",
                    "completed": completed,
                    "total": total,
                    "elapsed_ms": origin.elapsed().as_millis()
                }));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }

    for handle in handles {
        handle
            .join()
            .map_err(|_| "scenario operation thread panicked".to_owned())?;
    }
    if completed != total {
        return Err(format!(
            "scenario event stream ended early: completed {completed} of {total} operations"
        ));
    }
    let results = results
        .into_iter()
        .map(|value| value.expect("completed scenario operation has a result"))
        .collect::<Vec<_>>();
    let payload = json!({
        "status": "completed",
        "operation_count": total,
        "elapsed_ms": origin.elapsed().as_millis(),
        "results": results
    });
    if json_output {
        println!("{payload}");
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(&payload)
                .map_err(|error| format!("cannot encode scenario result: {error}"))?
        );
    }
    Ok(())
}

fn sleep_until(origin: Instant, at_ms: u64) {
    let target = origin + Duration::from_millis(at_ms);
    if let Some(remaining) = target.checked_duration_since(Instant::now()) {
        thread::sleep(remaining);
    }
}

fn emit_event(event: Value) {
    eprintln!("{event}");
}
