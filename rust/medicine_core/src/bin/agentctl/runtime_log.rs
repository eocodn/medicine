use medicine_core::RequestObservation;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::collections::hash_map::DefaultHasher;
use std::env;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::Duration;

const MAX_RETAINED_LOGS: i64 = 1_000;
const MAX_QUERY_LIMIT: usize = 500;

pub(super) fn record_or_emit(observation: &RequestObservation) {
    if let Err(detail) = record(observation, None) {
        eprintln!(
            "{}",
            json!({
                "event": "agentctl_log_failed",
                "detail": detail,
            })
        );
    }
}

pub(super) fn command(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let mut limit = 100usize;
    let mut log_db: Option<PathBuf> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--limit" => {
                index += 1;
                limit = args
                    .get(index)
                    .ok_or_else(usage)?
                    .parse::<usize>()
                    .map_err(|_| "logs limit must be a positive integer".to_owned())?;
                if limit == 0 || limit > MAX_QUERY_LIMIT {
                    return Err(format!(
                        "logs limit must be between 1 and {MAX_QUERY_LIMIT}"
                    ));
                }
            }
            "--log-db" => {
                index += 1;
                log_db = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let path = log_db.unwrap_or_else(default_path);
    let logs = snapshot(&path, limit)?;
    let payload = json!({
        "log_db": path,
        "limit": limit,
        "logs": logs,
    });
    if json_output {
        println!("{payload}");
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(&payload)
                .map_err(|error| format!("cannot encode runtime logs: {error}"))?
        );
    }
    Ok(())
}

fn record(observation: &RequestObservation, path: Option<&Path>) -> Result<(), String> {
    let path = path.map(Path::to_path_buf).unwrap_or_else(default_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create agentctl log directory: {error}"))?;
    }
    let mut con = Connection::open(&path)
        .map_err(|error| format!("cannot open agentctl runtime log: {error}"))?;
    con.busy_timeout(Duration::from_secs(3))
        .map_err(|error| format!("cannot configure agentctl runtime log: {error}"))?;
    con.execute_batch(
        "CREATE TABLE IF NOT EXISTS runtime_logs(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             recorded_at TEXT NOT NULL,
             method TEXT NOT NULL,
             path TEXT NOT NULL,
             access TEXT NOT NULL,
             status INTEGER NOT NULL,
             elapsed_ms INTEGER NOT NULL
         );",
    )
    .map_err(|error| format!("cannot initialize agentctl runtime log: {error}"))?;
    let transaction = con
        .transaction()
        .map_err(|error| format!("cannot begin agentctl runtime log write: {error}"))?;
    let elapsed_ms = i64::try_from(observation.elapsed_ms).unwrap_or(i64::MAX);
    transaction
        .execute(
            "INSERT INTO runtime_logs(recorded_at,method,path,access,status,elapsed_ms)
             VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,?,?)",
            params![
                observation.method,
                observation.path,
                observation.access,
                observation.status,
                elapsed_ms,
            ],
        )
        .map_err(|error| format!("cannot append agentctl runtime log: {error}"))?;
    transaction
        .execute(
            "DELETE FROM runtime_logs
             WHERE id <= COALESCE((SELECT MAX(id) FROM runtime_logs), 0) - ?",
            [MAX_RETAINED_LOGS],
        )
        .map_err(|error| format!("cannot bound agentctl runtime log: {error}"))?;
    transaction
        .commit()
        .map_err(|error| format!("cannot commit agentctl runtime log: {error}"))
}

fn snapshot(path: &Path, limit: usize) -> Result<Vec<Value>, String> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let con = Connection::open(path)
        .map_err(|error| format!("cannot open agentctl runtime log: {error}"))?;
    let mut statement = con
        .prepare(
            "SELECT id,recorded_at,method,path,access,status,elapsed_ms
             FROM runtime_logs ORDER BY id DESC LIMIT ?",
        )
        .map_err(|error| format!("cannot query agentctl runtime log: {error}"))?;
    let rows = statement
        .query_map([limit as i64], |row| {
            Ok(json!({
                "id": row.get::<_, i64>(0)?,
                "recorded_at": row.get::<_, String>(1)?,
                "method": row.get::<_, String>(2)?,
                "path": row.get::<_, String>(3)?,
                "access": row.get::<_, String>(4)?,
                "status": row.get::<_, i64>(5)?,
                "elapsed_ms": row.get::<_, i64>(6)?,
            }))
        })
        .map_err(|error| format!("cannot read agentctl runtime log: {error}"))?;
    let mut logs = rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("cannot decode agentctl runtime log: {error}"))?;
    logs.reverse();
    Ok(logs)
}

fn default_path() -> PathBuf {
    if let Some(path) = env::var_os("MEDICINE_AGENTCTL_LOG_DB") {
        return PathBuf::from(path);
    }
    let mut hasher = DefaultHasher::new();
    env::current_dir().unwrap_or_default().hash(&mut hasher);
    env::temp_dir().join(format!("medicine-agentctl-{:016x}.sqlite", hasher.finish()))
}
