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
        #[cfg(feature = "agentctl-web")]
        "ui-state",
        #[cfg(feature = "agentctl-web")]
        "ui-events",
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
        #[cfg(feature = "agentctl-web")]
        "ui-scenario",
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
    #[cfg(all(feature = "web", not(feature = "agentctl-web")))]
    let shared_ui = json!({
        "id": "shared-ui",
        "kind": "gui",
        "controls": ["screenshot"],
        "observations": ["screenshot"]
    });
    #[cfg(feature = "agentctl-web")]
    let shared_ui = json!({
        "id": "shared-ui",
        "kind": "gui",
        "controls": ["screenshot", "ui-scenario"],
        "observations": ["screenshot", "ui-state", "ui-events"],
        "ui_scenario": {
            "event_stream": ["ui_scenario_started", "ui_operation_started", "ui_operation_completed", "browser_event", "heartbeat"],
            "actions": ["open", "reload", "click", "set-field", "observe", "set-storage", "request", "fault", "wait-request", "release", "wait-state", "clock-set", "screenshot"],
            "fault_actions": ["fail", "delay", "gate"],
            "semantic_targets": [
                "screen:<home|meds|search|people>", "profile-shortcut", "add-person",
                "person:<id>", "person:<id>:edit", "person:<id>:delete", "person-name:<name>",
                "person:submit", "person:confirm-delete", "add-medication", "search:query",
                "product:<product_ref>", "prescription:open", "prescription:add-time",
                "prescription:save", "medication:<id>:edit", "medication:<id>:stop",
                "medication:confirm-stop", "dose:<id>:taken", "dose:<id>:skipped",
                "dose:<id>:cancel", "sheet:close"
            ],
            "fields": [
                "person:name", "person:birth_year", "person:birth_month", "person:birth_day",
                "person:sex", "person:pregnancy_status", "person:lactation_status", "person:notes",
                "search:query", "prescription:dose-amount", "prescription:dose-unit",
                "prescription:route", "prescription:prn", "prescription:frequency",
                "prescription:meal", "prescription:prn-max", "prescription:start-date",
                "prescription:long-term", "prescription:days", "prescription:time:<index>"
            ]
        }
    });
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
        shared_ui,
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
