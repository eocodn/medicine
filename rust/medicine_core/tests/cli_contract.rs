use serde_json::Value;
use std::process::Command;

#[test]
fn request_access_supports_json_for_agent_control() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args(["request-access", "GET", "/api/people", "--json"])
        .output()
        .expect("run medicine-core");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["access"], "personal_read");
}

#[test]
fn health_supports_json_for_agent_control() {
    let output = Command::new(env!("CARGO_BIN_EXE_medicine-core"))
        .args([
            "health",
            "--reference-unavailable-reason",
            "fixture",
            "--json",
        ])
        .output()
        .expect("run medicine-core");
    assert!(output.status.success());
    let value: Value = serde_json::from_slice(&output.stdout).expect("json output");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["reference_available"], false);
    assert_eq!(value["body"]["reference_status"], "fixture");
}
