#[cfg(feature = "agentctl")]
#[path = "app_commands.rs"]
mod app_commands;
#[cfg(feature = "agentctl")]
#[path = "discovery.rs"]
mod discovery;
#[path = "../medicine_core/reference_cli.rs"]
mod reference_cli;
#[cfg(feature = "agentctl")]
#[path = "scenario.rs"]
mod scenario;

use medicine_core::{
    assemble_dur_display, checkpoint_personal_db, initialize_personal_db,
    inspect_interaction_risks, inspect_product, inspect_profile_risks, inspect_safety_basis,
    normalize_prescription_draft, verify_reference_database, MedicineEngine,
    PERSONAL_SCHEMA_VERSION,
};
use serde_json::json;
use std::path::Path;

pub fn main_entry() {
    match run(std::env::args().skip(1).collect()) {
        Ok(0) => {}
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}

fn run(args: Vec<String>) -> Result<i32, String> {
    #[cfg(feature = "agentctl")]
    if let Some(code) = app_commands::try_run(&args, usage)? {
        return Ok(code);
    }
    let Some(command) = args.first().map(String::as_str) else {
        return Err(usage());
    };
    let result = match command {
        #[cfg(feature = "agentctl")]
        "capabilities" => discovery::capabilities(&args[1..], usage),
        #[cfg(feature = "agentctl")]
        "targets" => discovery::targets(&args[1..], usage),
        #[cfg(feature = "agentctl")]
        "scenario" => scenario::run(&args[1..], usage),
        "request-access" => request_access(&args[1..]),
        "request" => request(&args[1..]),
        "health" => health(&args[1..]),
        "personal-schema" => personal_maintenance(&args[1..], false),
        "personal-checkpoint" => personal_maintenance(&args[1..], true),
        "reference-verify" => reference_verify(&args[1..]),
        "reference-state" => reference_cli::reference_state(&args[1..], usage),
        "reference-apply" => reference_cli::reference_apply(&args[1..], usage),
        "product" => product(&args[1..]),
        "draft-normalize" => draft_normalize(&args[1..]),
        "safety-basis" => safety_basis(&args[1..]),
        "dur-display" => dur_display(&args[1..]),
        "profile-risks" => profile_risks(&args[1..]),
        "interaction-risks" => interaction_risks(&args[1..]),
        _ => Err(usage()),
    };
    result.map(|()| 0)
}

fn reference_verify(args: &[String]) -> Result<(), String> {
    let mut reference_db: Option<String> = None;
    let mut contract_major: Option<u64> = None;
    let mut dataset_id: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--reference-db" => {
                index += 1;
                reference_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--contract-major" => {
                index += 1;
                contract_major = Some(
                    args.get(index)
                        .ok_or_else(usage)?
                        .parse()
                        .map_err(|_| "contract major must be a positive integer".to_owned())?,
                );
            }
            "--dataset-id" => {
                index += 1;
                dataset_id = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let report = verify_reference_database(
        Path::new(&reference_db.ok_or_else(usage)?),
        contract_major.ok_or_else(usage)?,
        &dataset_id.ok_or_else(usage)?,
    )
    .map_err(|error| error.to_string())?;
    print_response(
        &json!({
            "status": 200,
            "body": {
                "status": report.status,
                "contract_major": report.contract_major,
                "dataset_id": report.dataset_id,
                "size_bytes": report.size_bytes,
            }
        })
        .to_string(),
        json_output,
    )
}

fn personal_maintenance(args: &[String], checkpoint: bool) -> Result<(), String> {
    let mut personal_db: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--personal-db" => {
                index += 1;
                personal_db = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let personal_db = personal_db.ok_or_else(usage)?;
    let path = Path::new(&personal_db);
    let body = if checkpoint {
        checkpoint_personal_db(path)?;
        json!({"checkpointed": true})
    } else {
        initialize_personal_db(path)?;
        json!({"initialized": true, "schema_version": PERSONAL_SCHEMA_VERSION})
    };
    print_response(
        &json!({"status": 200, "body": body}).to_string(),
        json_output,
    )
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

fn profile_risks(args: &[String]) -> Result<(), String> {
    let mut canonical_db: Option<String> = None;
    let mut product_ref: Option<String> = None;
    let mut person: Option<String> = None;
    let mut course: Option<String> = None;
    let mut as_of: Option<String> = None;
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
            "--course" => {
                index += 1;
                course = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--as-of" => {
                index += 1;
                as_of = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let canonical_db = canonical_db.ok_or_else(usage)?;
    let product_ref = product_ref.ok_or_else(usage)?;
    let person = person.ok_or_else(usage)?;
    let course = course.ok_or_else(usage)?;
    print_response(
        &inspect_profile_risks(
            Some(Path::new(&canonical_db)),
            &product_ref,
            &person,
            &course,
            as_of.as_deref(),
        ),
        json_output,
    )
}

fn interaction_risks(args: &[String]) -> Result<(), String> {
    let mut canonical_db: Option<String> = None;
    let mut product_ref: Option<String> = None;
    let mut current: Option<String> = None;
    let mut course: Option<String> = None;
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
            "--current" => {
                index += 1;
                current = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--course" => {
                index += 1;
                course = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }
    let canonical_db = canonical_db.ok_or_else(usage)?;
    let product_ref = product_ref.ok_or_else(usage)?;
    let current = current.ok_or_else(usage)?;
    let course = course.ok_or_else(usage)?;
    print_response(
        &inspect_interaction_risks(
            Some(Path::new(&canonical_db)),
            &product_ref,
            &current,
            &course,
        ),
        json_output,
    )
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
    let common = "medicine-agentctl request-access <METHOD> <PATH> [--json]\n       medicine-agentctl request <METHOD> <PATH> [--canonical-db <PATH>] [--personal-db <PATH>] [--body <JSON>] [--json]\n       medicine-agentctl health [--canonical-db <PATH>] [--reference-unavailable-reason <REASON>] [--json]\n       medicine-agentctl personal-schema --personal-db <PATH> [--json]\n       medicine-agentctl personal-checkpoint --personal-db <PATH> [--json]\n       medicine-agentctl reference-verify --reference-db <PATH> --contract-major <N> --dataset-id <SHA256> [--json]\n       medicine-agentctl reference-state --state-file <PATH> [--json]\n       medicine-agentctl reference-apply --kind <full|patch> --artifact <PATH> [--source <PATH>] --destination <PATH> --target-size <BYTES> --target-sha256 <SHA256> [--json]\n       medicine-agentctl product --canonical-db <PATH> --product-ref <REF> [--json]\n       medicine-agentctl draft-normalize --body <JSON> [--person <ID> --product-ref <REF>] [--json]\n       medicine-agentctl safety-basis --canonical-db <PATH> --product-ref <REF> --person <JSON> --draft <JSON> [--json]\n       medicine-agentctl dur-display --input <JSON> [--json]\n       medicine-agentctl profile-risks --canonical-db <PATH> --product-ref <REF> --person <JSON> --course <JSON> [--as-of <YYYY-MM-DD>] [--json]\n       medicine-agentctl interaction-risks --canonical-db <PATH> --product-ref <REF> --current <JSON> --course <JSON> [--json]";
    #[cfg(feature = "agentctl")]
    {
        format!(
            "usage: medicine-agentctl capabilities [--json]\n       medicine-agentctl targets [--json]\n       medicine-agentctl scenario --personal-db <PATH> [--canonical-db <PATH>] --input <JSON> [--json]\n       {common}"
        )
    }
    #[cfg(not(feature = "agentctl"))]
    {
        format!("usage: {common}")
    }
}
