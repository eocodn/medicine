use medicine_core::MedicineEngine;
use serde_json::json;
use std::path::Path;

fn main() {
    if let Err(error) = run(std::env::args().skip(1).collect()) {
        eprintln!("{error}");
        std::process::exit(2);
    }
}

fn run(args: Vec<String>) -> Result<(), String> {
    let Some(command) = args.first().map(String::as_str) else {
        return Err(usage());
    };
    match command {
        "request-access" => request_access(&args[1..]),
        "health" => health(&args[1..]),
        _ => Err(usage()),
    }
}

fn request_access(args: &[String]) -> Result<(), String> {
    if args.len() < 2 || args.len() > 3 {
        return Err(usage());
    }
    let json_output = args.get(2).is_some_and(|value| value == "--json");
    if args.len() == 3 && !json_output {
        return Err(usage());
    }
    let engine = MedicineEngine::new(None, None);
    let access = engine.request_access(&args[0], &args[1]).as_str();
    if json_output {
        println!("{}", json!({"access": access}));
    } else {
        println!("{access}");
    }
    Ok(())
}

fn health(args: &[String]) -> Result<(), String> {
    let mut canonical_db: Option<String> = None;
    let mut reason: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--reference-unavailable-reason" => {
                index += 1;
                reason = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let engine = MedicineEngine::new(canonical_db.as_deref().map(Path::new), reason.as_deref());
    let response = engine.request("GET", "/api/health", "");
    if json_output {
        println!("{response}");
    } else {
        let parsed: serde_json::Value = serde_json::from_str(&response)
            .map_err(|error| format!("cannot decode health response: {error}"))?;
        println!("{}", parsed["body"]);
    }
    Ok(())
}

fn usage() -> String {
    "usage: medicine-core request-access <METHOD> <PATH> [--json]\n       medicine-core health [--canonical-db <PATH>] [--reference-unavailable-reason <REASON>] [--json]".to_owned()
}
