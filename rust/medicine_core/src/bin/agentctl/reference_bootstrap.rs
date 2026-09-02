use medicine_core::reference_channel::{reference_channel_runtime, ReferenceChannelConfig};
use medicine_core::reference_lifecycle_runtime::ReferenceRuntimeResult;
use medicine_core::reference_manager::ReferenceUpdateStatus;
use serde_json::json;
use std::path::PathBuf;

pub(super) fn command(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let Some(action) = args.first().map(String::as_str) else {
        return Err(usage());
    };
    if !matches!(action, "status" | "start" | "update") {
        return Err(usage());
    }

    let mut reference_dir = None;
    let mut base_url = None;
    let mut trust_manifest = None;
    let mut json_output = false;
    let mut index = 1;
    while index < args.len() {
        match args[index].as_str() {
            "--reference-dir" => {
                index += 1;
                reference_dir = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--base-url" => {
                index += 1;
                base_url = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--trust-manifest" => {
                index += 1;
                trust_manifest = Some(PathBuf::from(args.get(index).ok_or_else(usage)?));
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }

    let runtime = reference_channel_runtime(ReferenceChannelConfig {
        reference_dir: reference_dir.ok_or_else(usage)?,
        base_url: base_url.ok_or_else(usage)?,
        trust_manifest: trust_manifest.ok_or_else(usage)?,
    })
    .map_err(|error| error.to_string())?;

    match action {
        "status" => emit_runtime_result(
            action,
            runtime.prepare().map_err(|error| error.to_string())?,
            json_output,
        ),
        "start" => emit_runtime_result(
            action,
            runtime.start().map_err(|error| error.to_string())?,
            json_output,
        ),
        "update" => {
            let prepared = runtime.prepare().map_err(|error| error.to_string())?;
            if !matches!(
                prepared.snapshot.state,
                medicine_core::ReferenceBootstrapState::Ready
            ) {
                return emit(
                    json!({
                        "action": action,
                        "snapshot": prepared.snapshot,
                        "update_status": null,
                    }),
                    json_output,
                );
            }
            let update_status = match runtime
                .check_for_update()
                .map_err(|error| error.to_string())?
            {
                ReferenceUpdateStatus::NoChange => "no_change",
                ReferenceUpdateStatus::Staged => "staged",
                ReferenceUpdateStatus::UpdateRequired => "update_required",
            };
            emit(
                json!({
                    "action": action,
                    "snapshot": runtime.status(),
                    "update_status": update_status,
                }),
                json_output,
            )
        }
        _ => unreachable!(),
    }
}

fn emit_runtime_result(
    action: &str,
    result: ReferenceRuntimeResult,
    json_output: bool,
) -> Result<(), String> {
    let (database, unavailable_reason) = result
        .selection
        .map(|selection| {
            (
                selection.database.map(|path| path.display().to_string()),
                selection.unavailable_reason,
            )
        })
        .unwrap_or((None, None));
    emit(
        json!({
            "action": action,
            "snapshot": result.snapshot,
            "database": database,
            "unavailable_reason": unavailable_reason,
        }),
        json_output,
    )
}

fn emit(payload: serde_json::Value, json_output: bool) -> Result<(), String> {
    if json_output {
        println!("{payload}");
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(&payload)
                .map_err(|error| format!("cannot encode agentctl output: {error}"))?
        );
    }
    Ok(())
}
