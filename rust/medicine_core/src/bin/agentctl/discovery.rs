use serde_json::json;

pub(super) fn capabilities(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let json_output = json_flag(args, usage)?;
    #[cfg(not(feature = "web"))]
    let observation = vec![
        "health",
        "reference-state",
        "reference-bootstrap",
        "scenario-events",
        "logs",
    ];
    #[cfg(feature = "web")]
    let observation = vec![
        "health",
        "reference-state",
        "reference-bootstrap",
        "scenario-events",
        "logs",
        "screenshot",
    ];
    #[cfg(not(feature = "web"))]
    let control = vec![
        "request",
        "personal-schema",
        "personal-checkpoint",
        "reference-verify",
        "reference-apply",
        "reference-bootstrap",
        "product",
        "draft-normalize",
        "safety-basis",
        "dur-display",
        "profile-risks",
        "interaction-risks",
        "scenario",
        "app-commands",
    ];
    #[cfg(feature = "web")]
    let control = vec![
        "request",
        "personal-schema",
        "personal-checkpoint",
        "reference-verify",
        "reference-apply",
        "reference-bootstrap",
        "product",
        "draft-normalize",
        "safety-basis",
        "dur-display",
        "profile-risks",
        "interaction-risks",
        "scenario",
        "app-commands",
        "screenshot",
    ];
    let payload = json!({
        "agentctl": true,
        "structured_output": true,
        "scheduled_operations": true,
        "max_scheduled_operations": 64,
        "max_schedule_horizon_ms": 60_000,
        "observation": observation,
        "control": control,
    });
    emit(&payload, json_output)
}

pub(super) fn targets(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let json_output = json_flag(args, usage)?;
    #[cfg(not(feature = "web"))]
    let targets = vec![
        json!({
            "id": "medicine-engine",
            "kind": "headless-core",
            "controls": ["request", "scenario"],
            "observations": ["health", "logs"]
        }),
        json!({
            "id": "reference-store",
            "kind": "state-store",
            "controls": ["reference-apply", "reference-bootstrap"],
            "observations": ["reference-state", "reference-bootstrap"]
        }),
    ];
    #[cfg(feature = "web")]
    let targets = vec![
        json!({
            "id": "medicine-engine",
            "kind": "headless-core",
            "controls": ["request", "scenario"],
            "observations": ["health", "logs"]
        }),
        json!({
            "id": "reference-store",
            "kind": "state-store",
            "controls": ["reference-apply", "reference-bootstrap"],
            "observations": ["reference-state", "reference-bootstrap"]
        }),
        json!({
            "id": "shared-ui",
            "kind": "gui",
            "controls": ["screenshot"],
            "observations": ["screenshot"]
        }),
    ];
    emit(&json!({"targets": targets}), json_output)
}

fn json_flag(args: &[String], usage: fn() -> String) -> Result<bool, String> {
    match args {
        [] => Ok(false),
        [flag] if flag == "--json" => Ok(true),
        _ => Err(usage()),
    }
}

fn emit(payload: &serde_json::Value, json_output: bool) -> Result<(), String> {
    if json_output {
        println!("{payload}");
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(payload)
                .map_err(|error| format!("cannot encode agentctl output: {error}"))?
        );
    }
    Ok(())
}
