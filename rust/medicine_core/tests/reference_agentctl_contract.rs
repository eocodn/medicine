#![cfg(feature = "agentctl")]

use serde_json::Value;
use std::process::Command;

fn agentctl() -> Command {
    Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
}

#[test]
fn reference_bootstrap_is_exposed_as_headless_control_and_observation() {
    let capabilities = agentctl()
        .args(["capabilities", "--json"])
        .output()
        .expect("run agentctl capabilities");
    assert!(capabilities.status.success());
    let capabilities: Value =
        serde_json::from_slice(&capabilities.stdout).expect("capabilities json");
    assert!(capabilities["control"]
        .as_array()
        .expect("control list")
        .iter()
        .any(|item| item == "reference-bootstrap"));
    assert!(capabilities["observation"]
        .as_array()
        .expect("observation list")
        .iter()
        .any(|item| item == "reference-bootstrap"));

    let targets = agentctl()
        .args(["targets", "--json"])
        .output()
        .expect("run agentctl targets");
    assert!(targets.status.success());
    let targets: Value = serde_json::from_slice(&targets.stdout).expect("targets json");
    let reference_store = targets["targets"]
        .as_array()
        .expect("targets array")
        .iter()
        .find(|target| target["id"] == "reference-store")
        .expect("reference-store target");
    assert!(reference_store["controls"]
        .as_array()
        .expect("reference-store controls")
        .iter()
        .any(|item| item == "reference-bootstrap"));
    assert!(reference_store["observations"]
        .as_array()
        .expect("reference-store observations")
        .iter()
        .any(|item| item == "reference-bootstrap"));
}

#[test]
fn reference_bootstrap_command_is_wired_to_shared_runtime_validation() {
    let result = agentctl()
        .args([
            "reference-bootstrap",
            "status",
            "--reference-dir",
            "/tmp/medicine-agentctl-reference-bootstrap",
            "--base-url",
            "https://example.invalid/",
            "--trust-manifest",
            "/tmp/missing-reference-trust.json",
            "--contract-major",
            "0",
            "--json",
        ])
        .output()
        .expect("run agentctl reference bootstrap validation");
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr)
        .contains("reference contract major must be positive"));
}
