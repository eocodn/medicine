#[path = "app_commands/commands.rs"]
mod commands;
#[cfg(feature = "web")]
#[path = "app_commands/screenshot.rs"]
mod screenshot;
#[path = "app_commands/support.rs"]
mod support;

use support::{emit_body, exit_code, parse_global_options};

const APP_COMMANDS: &[&str] = &[
    "people",
    "person-add",
    "person-update",
    "person-delete",
    "drug-search",
    "meds",
    "risk-preview",
    "med-add",
    "med-update",
    "med-history",
    "med-stop",
    "daily-plan",
    "reminders",
    "reminder-resolve",
    "dose-instance",
    "dose-instance-cancel",
    "prn-intake",
    #[cfg(feature = "web")]
    "screenshot",
];

pub fn try_run(args: &[String], usage: fn() -> String) -> Result<Option<i32>, String> {
    let (config, command_index) = parse_global_options(args, usage)?;
    let Some(command) = args.get(command_index).map(String::as_str) else {
        return Ok(None);
    };
    if !APP_COMMANDS.contains(&command) {
        if command_index == 0 {
            return Ok(None);
        }
        return Err(usage());
    }
    let command_args = &args[command_index + 1..];
    #[cfg(feature = "web")]
    let envelope = if command == "screenshot" {
        screenshot::capture(&config, command_args, usage)?
    } else {
        commands::dispatch(&config, command, command_args, usage)?
    };
    #[cfg(not(feature = "web"))]
    let envelope = commands::dispatch(&config, command, command_args, usage)?;
    emit_body(&envelope, command_args.iter().any(|arg| arg == "--json"))?;
    Ok(Some(exit_code(&envelope)))
}
