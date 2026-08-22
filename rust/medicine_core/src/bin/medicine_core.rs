use medicine_core::{
    assemble_dur_display, inspect_product, inspect_safety_basis, normalize_prescription_draft,
    MedicineEngine,
};
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
        "request" => request(&args[1..]),
        "health" => health(&args[1..]),
        "product" => product(&args[1..]),
        "draft-normalize" => draft_normalize(&args[1..]),
        "safety-basis" => safety_basis(&args[1..]),
        "dur-display" => dur_display(&args[1..]),
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
    let engine = MedicineEngine::new(None, None, None);
    let access = engine.request_access(&args[0], &args[1]).as_str();
    if json_output {
        println!("{}", json!({"access": access}));
    } else {
        println!("{access}");
    }
    Ok(())
}

fn request(args: &[String]) -> Result<(), String> {
    if args.len() < 2 {
        return Err(usage());
    }
    let method = args[0].as_str();
    let path = args[1].as_str();
    let mut canonical_db: Option<String> = None;
    let mut personal_db: Option<String> = None;
    let mut body = String::new();
    let mut json_output = false;
    let mut index = 2;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--personal-db" => {
                index += 1;
                personal_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--body" => {
                index += 1;
                body = args.get(index).ok_or_else(usage)?.to_owned();
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let engine = MedicineEngine::new(
        canonical_db.as_deref().map(Path::new),
        personal_db.as_deref().map(Path::new),
        None,
    );
    let response = engine.request(method, path, &body);
    if json_output {
        println!("{response}");
    } else {
        let parsed: serde_json::Value = serde_json::from_str(&response)
            .map_err(|error| format!("cannot decode response: {error}"))?;
        println!("{}", parsed["body"]);
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
    let engine = MedicineEngine::new(
        canonical_db.as_deref().map(Path::new),
        None,
        reason.as_deref(),
    );
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

fn product(args: &[String]) -> Result<(), String> {
    let mut canonical_db: Option<String> = None;
    let mut product_ref: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--product-ref" => {
                index += 1;
                product_ref = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let canonical_db = canonical_db.ok_or_else(usage)?;
    let product_ref = product_ref.ok_or_else(usage)?;
    print_response(
        &inspect_product(Some(Path::new(&canonical_db)), &product_ref),
        json_output,
    )
}

fn draft_normalize(args: &[String]) -> Result<(), String> {
    let mut body: Option<String> = None;
    let mut person_id: Option<String> = None;
    let mut product_ref: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--body" => {
                index += 1;
                body = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--person" => {
                index += 1;
                person_id = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--product-ref" => {
                index += 1;
                product_ref = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    if person_id.is_some() != product_ref.is_some() {
        return Err("--person and --product-ref must be supplied together".to_owned());
    }
    let body = body.ok_or_else(usage)?;
    print_response(
        &normalize_prescription_draft(&body, person_id.as_deref(), product_ref.as_deref()),
        json_output,
    )
}

fn safety_basis(args: &[String]) -> Result<(), String> {
    let mut canonical_db: Option<String> = None;
    let mut product_ref: Option<String> = None;
    let mut person: Option<String> = None;
    let mut draft: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--canonical-db" => {
                index += 1;
                canonical_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--product-ref" => {
                index += 1;
                product_ref = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--person" => {
                index += 1;
                person = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--draft" => {
                index += 1;
                draft = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let canonical_db = canonical_db.ok_or_else(usage)?;
    let product_ref = product_ref.ok_or_else(usage)?;
    let person = person.ok_or_else(usage)?;
    let draft = draft.ok_or_else(usage)?;
    print_response(
        &inspect_safety_basis(
            Some(Path::new(&canonical_db)),
            &product_ref,
            &person,
            &draft,
        ),
        json_output,
    )
}

fn dur_display(args: &[String]) -> Result<(), String> {
    let mut input: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--input" => {
                index += 1;
                input = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let input = input.ok_or_else(usage)?;
    print_response(&assemble_dur_display(&input), json_output)
}

fn print_response(response: &str, json_output: bool) -> Result<(), String> {
    if json_output {
        println!("{response}");
    } else {
        let parsed: serde_json::Value = serde_json::from_str(response)
            .map_err(|error| format!("cannot decode response: {error}"))?;
        println!("{}", parsed["body"]);
    }
    Ok(())
}

fn usage() -> String {
    "usage: medicine-core request-access <METHOD> <PATH> [--json]\n       medicine-core request <METHOD> <PATH> [--canonical-db <PATH>] [--personal-db <PATH>] [--body <JSON>] [--json]\n       medicine-core health [--canonical-db <PATH>] [--reference-unavailable-reason <REASON>] [--json]\n       medicine-core product --canonical-db <PATH> --product-ref <REF> [--json]\n       medicine-core draft-normalize --body <JSON> [--person <ID> --product-ref <REF>] [--json]\n       medicine-core safety-basis --canonical-db <PATH> --product-ref <REF> --person <JSON> --draft <JSON> [--json]\n       medicine-core dur-display --input <JSON> [--json]".to_owned()
}
